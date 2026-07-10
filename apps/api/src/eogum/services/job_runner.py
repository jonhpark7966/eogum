"""Lane-based job runner for processing video projects."""

import json
import logging
import hashlib
import subprocess
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET

from eogum.config import settings
from eogum.services import (
    avid,
    chalna,
    credit,
    email,
    overlap_protection as overlap_detection,
    overlap_speaker_mapping,
    r2,
    scribe_v2_cache,
    source_cache,
    source_derivatives,
)
from eogum.services.artifacts import get_latest_artifact_job
from eogum.services.database import execute_with_retry, get_db
from eogum.services.final_preview_cache import (
    final_preview_decision_hash,
    new_cache_token,
    preview_cache_key,
    preview_cache_paths,
    preview_cache_ready,
    source_cache_path,
)

logger = logging.getLogger(__name__)

_job_lanes = ("project", "reprocess", "source_derive", "cut_decision", "final_preview")
_queues: dict[str, deque[dict[str, str | None]]] = {lane: deque() for lane in _job_lanes}
_running_lanes: dict[str, int] = {lane: 0 for lane in _job_lanes}
_worker_limit_settings = {
    "project": "project_worker_count",
    "reprocess": "reprocess_worker_count",
    "source_derive": "source_derive_worker_count",
    "cut_decision": "cut_decision_worker_count",
    "final_preview": "final_preview_worker_count",
}
_lock = threading.Lock()
PODCAST_LIKE_CUT_TYPES = frozenset({"podcast_cut", "ai_frontier_cut"})
_PODCAST_PROMPT_PROFILES = {
    "podcast_cut": "podcast",
    "ai_frontier_cut": "ai_frontier",
}
_initial_job_types = {"subtitle_cut", *PODCAST_LIKE_CUT_TYPES}
_incomplete_job_statuses = ["queued", "pending", "running"]
_stale_running_after = timedelta(hours=6)
_PODCAST_CUT_RESUME_MARKER = "resume_state.json"
ALLOWED_SEGMENTATION_BOUNDARY_RULES = {
    "word_boundary",
    "midpoint_gap",
    "low_energy_gap_v1",
}
DEFAULT_SEGMENTATION_BOUNDARY_RULE = "word_boundary"
FINAL_PREVIEW_MERGE_GAP_MS = 500
CHALNA_MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024


class AudioProxyPreparationError(RuntimeError):
    """Raised when a source cannot provide a valid Chalna audio proxy."""


def _resolve_cut_runner(project: dict):
    """Return the AVID cut function and style-only keyword arguments."""
    cut_type = project.get("cut_type")
    if cut_type == "subtitle_cut":
        return avid.subtitle_cut, {}

    prompt_profile = _PODCAST_PROMPT_PROFILES.get(cut_type)
    if prompt_profile is not None:
        return avid.podcast_cut, {"prompt_profile": prompt_profile}

    raise ValueError(f"Unsupported cut_type: {cut_type}")


def enqueue(project_id: str, job_id: str) -> None:
    """Add project to processing queue."""
    _enqueue("initial", project_id, job_id)


def enqueue_reprocess(project_id: str, job_id: str) -> None:
    """Add reprocess task to queue."""
    _enqueue("reprocess", project_id, job_id)


def enqueue_cut_decision(project_id: str, job_id: str) -> None:
    """Add cut-decision-only task to queue."""
    _enqueue("cut_decision", project_id, job_id)


def enqueue_final_preview(project_id: str, job_id: str) -> None:
    """Add final-preview render task to queue."""
    _enqueue("final_preview", project_id, job_id)


def enqueue_source_derive(project_id: str, job_id: str) -> None:
    """Add source-derivative generation task to queue."""
    _enqueue("source_derive", project_id, job_id)


def _lane_for_kind(kind: str) -> str:
    if kind == "reprocess":
        return "reprocess"
    if kind == "source_derive":
        return "source_derive"
    if kind == "cut_decision":
        return "cut_decision"
    if kind == "final_preview":
        return "final_preview"
    return "project"


def _enqueue(kind: str, project_id: str, job_id: str | None) -> None:
    lane = _lane_for_kind(kind)
    with _lock:
        _queues[lane].append({"kind": kind, "project_id": project_id, "job_id": job_id})
    _maybe_start_workers(lane)


def _lane_worker_limit(lane: str) -> int:
    value = getattr(settings, _worker_limit_settings[lane], 1)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        logger.warning("Invalid worker limit for lane %s: %r; using 1", lane, value)
        return 1


def _maybe_start_workers(lane: str) -> None:
    threads_to_start = 0
    with _lock:
        limit = _lane_worker_limit(lane)
        while _queues[lane] and _running_lanes[lane] < limit:
            _running_lanes[lane] += 1
            threads_to_start += 1

    for _ in range(threads_to_start):
        thread = threading.Thread(target=_worker_loop, args=(lane,), daemon=True)
        thread.start()


def _worker_loop(lane: str) -> None:
    while True:
        with _lock:
            if not _queues[lane]:
                _running_lanes[lane] -= 1
                return
            item = _queues[lane].popleft()
        project_id = item["project_id"]
        try:
            if item["kind"] == "reprocess":
                _reprocess_project(project_id, item["job_id"])
            elif item["kind"] == "source_derive":
                _derive_project_sources(project_id, item["job_id"])
            elif item["kind"] == "cut_decision":
                _cut_decision_project(project_id, item["job_id"])
            elif item["kind"] == "final_preview":
                _render_final_preview(project_id, item["job_id"])
            else:
                _process_project(project_id, item["job_id"])
        except Exception:
            logger.exception("Fatal error processing project %s", project_id)


def create_initial_job(
    db,
    project: dict,
    *,
    retry_of_job_id: str | None = None,
    attempt_number: int = 1,
) -> dict:
    """Create the durable queue record for an initial project run."""
    project_settings = project.get("settings") or {}
    payload = {
        "project_id": project["id"],
        "user_id": project["user_id"],
        "type": project["cut_type"],
        "status": "pending",
        "progress": 0,
        "pipeline_stages": _initial_pipeline_stages(
            use_llm_segmentation=_bool_project_setting(
                project_settings,
                "use_llm_segmentation",
                default=True,
            ),
            use_llm_refinement=_bool_project_setting(
                project_settings,
                "use_llm_refinement",
                default=True,
            ),
        ),
        "external_task_ids": {},
        "attempt_number": max(1, int(attempt_number)),
    }
    if retry_of_job_id:
        payload["retry_of_job_id"] = retry_of_job_id
    job = db.table("jobs").insert(payload).execute().data[0]
    return job


def create_cut_decision_job(db, project: dict) -> dict:
    """Create the durable queue record for rerunning edit decisions only."""
    job = db.table("jobs").insert({
        "project_id": project["id"],
        "user_id": project["user_id"],
        "type": "cut_decision",
        "status": "pending",
        "progress": 0,
        "pipeline_stages": _cut_decision_pipeline_stages(),
        "external_task_ids": {},
    }).execute().data[0]
    return job


def create_source_derive_job(
    db,
    project: dict,
    *,
    source_keys: list[str] | None = None,
    force: bool = False,
) -> dict:
    """Create the durable queue record for source derivative generation."""
    job = db.table("jobs").insert({
        "project_id": project["id"],
        "user_id": project["user_id"],
        "type": "source_derive",
        "status": "pending",
        "progress": 0,
        "input_payload": {
            "source_keys": source_keys or source_derivatives.source_keys_needing_derivatives(project, force=force),
            "force": force,
        },
    }).execute().data[0]
    return job


def recover_stuck_projects(*, recover_running: bool = False) -> int:
    """Requeue projects whose durable job state and project status diverged."""
    db = get_db()
    stuck = (
        db.table("projects")
        .select("*")
        .in_("status", ["queued", "processing"])
        .execute()
    )

    recovered = 0
    for project in stuck.data or []:
        try:
            if _recover_stuck_project(db, project, recover_running=recover_running):
                recovered += 1
        except Exception:
            logger.exception("Failed to recover stuck project %s", project.get("id"))
    return recovered


def recover_stuck_final_previews(*, recover_running: bool = False) -> int:
    """Requeue final-preview jobs that were left behind by a process restart."""
    db = get_db()
    jobs = (
        db.table("jobs")
        .select("id, project_id, status, started_at, created_at")
        .eq("type", "final_preview")
        .in_("status", _incomplete_job_statuses)
        .order("created_at")
        .execute()
        .data
        or []
    )

    recovered = 0
    for job in jobs:
        try:
            if job["status"] == "running" and not _should_recover_running_job(job, recover_running):
                continue
            db.table("jobs").update({
                "status": "pending",
                "progress": 0,
                "error_message": None,
                "started_at": None,
                "completed_at": None,
            }).eq("id", job["id"]).execute()
            enqueue_final_preview(job["project_id"], job["id"])
            recovered += 1
            logger.info(
                "Requeued stuck final-preview job %s for project %s",
                job["id"],
                job["project_id"],
            )
        except Exception:
            logger.exception("Failed to recover final-preview job %s", job.get("id"))
    return recovered


def recover_stuck_source_derivatives(*, recover_running: bool = False) -> int:
    """Requeue source-derive jobs that were left behind by a process restart."""
    db = get_db()
    jobs = (
        db.table("jobs")
        .select("id, project_id, status, started_at, created_at")
        .eq("type", "source_derive")
        .in_("status", _incomplete_job_statuses)
        .order("created_at")
        .execute()
        .data
        or []
    )

    recovered = 0
    for job in jobs:
        try:
            if job["status"] == "running" and not _should_recover_running_job(job, recover_running):
                continue
            db.table("jobs").update({
                "status": "pending",
                "progress": 0,
                "error_message": None,
                "started_at": None,
                "completed_at": None,
            }).eq("id", job["id"]).execute()
            enqueue_source_derive(job["project_id"], job["id"])
            recovered += 1
            logger.info(
                "Requeued stuck source-derive job %s for project %s",
                job["id"], job["project_id"],
            )
        except Exception:
            logger.exception("Failed to recover source-derive job %s", job.get("id"))
    return recovered


def start_stuck_project_sweeper(interval_seconds: int = 60) -> threading.Event:
    """Start a background sweeper for orphaned queued jobs."""
    stop_event = threading.Event()

    def _loop() -> None:
        while not stop_event.wait(interval_seconds):
            try:
                recovered = recover_stuck_projects(recover_running=False)
                if recovered:
                    logger.info("Recovered %d stuck project(s)", recovered)
                recovered_previews = recover_stuck_final_previews(recover_running=False)
                if recovered_previews:
                    logger.info("Recovered %d stuck final-preview job(s)", recovered_previews)
                recovered_derivatives = recover_stuck_source_derivatives(recover_running=False)
                if recovered_derivatives:
                    logger.info("Recovered %d stuck source-derive job(s)", recovered_derivatives)
            except Exception:
                logger.exception("Stuck job sweeper failed")

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return stop_event


def _recover_stuck_project(db, project: dict, *, recover_running: bool) -> bool:
    project_id = project["id"]
    latest = (
        db.table("jobs")
        .select("id, type, status, started_at, created_at")
        .eq("project_id", project_id)
        .in_("status", _incomplete_job_statuses)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    job = latest.data[0] if latest.data else None

    if not job:
        if project["status"] != "queued":
            db.table("projects").update({"status": "queued"}).eq("id", project_id).execute()
        job = create_initial_job(db, project)
        enqueue(project_id, job["id"])
        logger.info("Created missing pending job %s for stuck project %s", job["id"], project_id)
        return True

    job_type = job["type"]
    if job["status"] == "running" and not _should_recover_running_job(job, recover_running):
        return False

    if job_type == "reprocess_multicam":
        db.table("jobs").update({
            "status": "pending",
            "progress": 0,
            "error_message": None,
            "started_at": None,
            "completed_at": None,
        }).eq("id", job["id"]).execute()
        db.table("projects").update({"status": "processing"}).eq("id", project_id).execute()
        enqueue_reprocess(project_id, job["id"])
        logger.info("Requeued stuck reprocess job %s for project %s", job["id"], project_id)
        return True

    if job_type == "source_derive":
        db.table("jobs").update({
            "status": "pending",
            "progress": 0,
            "error_message": None,
            "started_at": None,
            "completed_at": None,
        }).eq("id", job["id"]).execute()
        enqueue_source_derive(project_id, job["id"])
        logger.info("Requeued stuck source-derive job %s for project %s", job["id"], project_id)
        return True

    if job_type == "cut_decision":
        db.table("jobs").update({
            "status": "pending",
            "progress": 0,
            "error_message": None,
            "started_at": None,
            "completed_at": None,
            "result_r2_keys": None,
            "pipeline_stages": _cut_decision_pipeline_stages(),
            "external_task_ids": {},
        }).eq("id", job["id"]).execute()
        db.table("projects").update({"status": "processing"}).eq("id", project_id).execute()
        enqueue_cut_decision(project_id, job["id"])
        logger.info("Requeued stuck cut-decision job %s for project %s", job["id"], project_id)
        return True

    if job_type in _initial_job_types:
        db.table("jobs").update({
            "status": "pending",
            "progress": 0,
            "error_message": None,
            "started_at": None,
            "completed_at": None,
            "result_r2_keys": None,
            "pipeline_stages": _initial_pipeline_stages(
                use_llm_segmentation=_bool_project_setting(
                    project.get("settings") or {},
                    "use_llm_segmentation",
                    default=True,
                ),
                use_llm_refinement=_bool_project_setting(
                    project.get("settings") or {},
                    "use_llm_refinement",
                    default=True,
                ),
            ),
            "external_task_ids": {},
        }).eq("id", job["id"]).execute()
        db.table("projects").update({"status": "queued"}).eq("id", project_id).execute()
        enqueue(project_id, job["id"])
        logger.info("Requeued stuck initial job %s for project %s", job["id"], project_id)
        return True

    return False


def _should_recover_running_job(job: dict, recover_running: bool) -> bool:
    if recover_running:
        return True
    started_at = _parse_datetime(job.get("started_at") or job.get("created_at"))
    if not started_at:
        return False
    return datetime.now(timezone.utc) - started_at > _stale_running_after


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _mark_initial_project_failure(db, project_id: str, *, job_id: str | None, error_message: str) -> None:
    """Best-effort cleanup for failures before normal job failure handling starts."""
    resolved_job_id = job_id
    if not resolved_job_id:
        latest_incomplete = (
            db.table("jobs")
            .select("id")
            .eq("project_id", project_id)
            .in_("status", _incomplete_job_statuses)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if latest_incomplete.data:
            resolved_job_id = latest_incomplete.data[0]["id"]

    if resolved_job_id:
        db.table("jobs").update({
            "status": "failed",
            "error_message": error_message[:1000],
            "completed_at": "now()",
        }).eq("id", resolved_job_id).execute()

    db.table("projects").update({"status": "failed"}).eq("id", project_id).execute()


def _process_project(project_id: str, job_id: str | None) -> None:
    if not job_id:
        raise RuntimeError("initial job_id is required")

    db = get_db()
    temp_dir = settings.avid_temp_dir / project_id
    project = None
    user_id = None
    user_email = None
    duration = 0
    credits_held = False
    current_stage: str | None = None
    resume_state: dict | None = None

    try:
        claimed = (
            db.table("jobs")
            .update({
                "status": "running",
                "progress": 0,
                "error_message": None,
                "started_at": "now()",
            })
            .eq("id", job_id)
            .eq("project_id", project_id)
            .in_("status", ["queued", "pending"])
            .execute()
        )
        if not claimed.data:
            logger.info("Initial job %s for project %s was already claimed or finished", job_id, project_id)
            return

        db.table("projects").update({"status": "processing"}).eq("id", project_id).execute()

        # Load project
        project = db.table("projects").select("*").eq("id", project_id).single().execute().data
        user_id = project["user_id"]
        duration = project["source_duration_seconds"]

        # Email lookup is best effort. Notification failures must not block processing.
        try:
            auth_user = db.auth.admin.get_user_by_id(user_id)
            user_email = auth_user.user.email
        except Exception:
            logger.exception("Failed to resolve user email for project %s", project_id)

        # Ensure temp dirs
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_dir = temp_dir / "output"
        output_dir.mkdir(exist_ok=True)
        llm_log_path = output_dir / "llm_io.jsonl"
        resume_state = _load_podcast_cut_resume_state(temp_dir, project)

        # 1. Hold credits
        credit.hold_credits(user_id, duration, job_id)
        credits_held = True

        # 2. Download source from R2
        current_stage = "source_download"
        source_ext = Path(project["source_filename"]).suffix
        source_path_obj = temp_dir / f"source{source_ext}"
        source_path = str(source_path_obj)
        _update_progress(db, job_id, 5)

        if resume_state and _local_source_matches_resume_state(source_path_obj, resume_state):
            logger.info("Reusing local source for podcast-cut retry project %s", project_id)
        else:
            r2.download_file(project["source_r2_key"], source_path)
        source_sha256 = _register_source_identity(
            db,
            project_id=project_id,
            project=project,
            source_path=source_path,
        )
        _derive_primary_source_best_effort(
            db,
            project_id=project_id,
            project=project,
            source_path=source_path_obj,
            source_sha256=source_sha256,
        )
        if resume_state and not _resume_state_source_matches_project(resume_state, project):
            logger.warning("Discarding podcast-cut resume state for project %s after source validation", project_id)
            resume_state = None

        # 2.5. Download extra sources (multicam)
        extra_source_paths: list[str] = []
        used_extra_names: set[str] = set()
        for i, es in enumerate(project.get("extra_sources") or []):
            local_path = str(_local_extra_source_path(temp_dir, es, i, used_extra_names))
            r2.download_file(es["r2_key"], local_path)
            extra_source_paths.append(local_path)

        _update_progress(db, job_id, 10)

        # 3. Transcribe
        current_stage = "transcription"
        project_settings = project.get("settings") or {}
        transcription_context = project_settings.get("transcription_context")
        overlap_protection_enabled = _bool_project_setting(
            project_settings,
            "overlap_protection_enabled",
            default=False,
        )
        overlap_artifact_path: Path | None = None
        overlap_detection_metadata: dict | None = None
        use_llm_refinement = _bool_project_setting(
            project_settings,
            "use_llm_refinement",
            default=True,
        )
        use_llm_segmentation = _bool_project_setting(
            project_settings,
            "use_llm_segmentation",
            default=True,
        )
        if overlap_protection_enabled:
            overlap_artifact_path, overlap_detection_metadata = (
                overlap_detection.build_overlap_protection_artifact(
                    source_path,
                    temp_dir / "overlap_protection",
                )
            )
            if overlap_detection_metadata.get("status") == "partial":
                logger.warning(
                    "Overlap protection partially succeeded for project %s: %s",
                    project_id,
                    {
                        key: value.get("status")
                        for key, value in (overlap_detection_metadata.get("models") or {}).items()
                        if isinstance(value, dict)
                    },
                )
        if resume_state and overlap_protection_enabled:
            logger.info(
                "Ignoring podcast-cut resume state for project %s because overlap protection requires segments metadata",
                project_id,
            )
            resume_state = None
        if resume_state:
            srt_path = str(_podcast_cut_resume_srt_path(temp_dir))
            transcription_result = _podcast_cut_resume_transcription_result(srt_path, resume_state)
            logger.info("Resuming project %s from podcast-cut using %s", project_id, srt_path)
        else:
            transcription_result = None
            if not overlap_protection_enabled:
                transcription_result = _download_reused_transcription_srt(
                    db,
                    job_id=job_id,
                    project_settings=project_settings,
                    output_dir=temp_dir,
                )
            if transcription_result is None:
                transcription_source_path = _ensure_chalna_audio_proxy(
                    project=project,
                    source_path=source_path_obj,
                    temp_dir=temp_dir,
                )
                transcription_result = _transcribe_with_scribe_v2_cache(
                    db,
                    job_id=job_id,
                    project=project,
                    source_path=str(transcription_source_path),
                    output_dir=temp_dir,
                    source_sha256=source_sha256,
                    language=project["language"],
                    transcription_context=transcription_context,
                    diarize=_bool_project_setting(project_settings, "diarize", default=True),
                    tag_audio_events=_bool_project_setting(project_settings, "tag_audio_events", default=True),
                    num_speakers=_optional_int_project_setting(project_settings, "num_speakers"),
                    use_llm_segmentation=use_llm_segmentation,
                    use_llm_refinement=use_llm_refinement,
                    bypass_llm_segmentation_cache=_bool_project_setting(
                        project_settings,
                        "bypass_llm_segmentation_cache",
                        default=False,
                    ),
                    segmentation_boundary_rule=_output_segmentation_boundary_rule(project),
                    llm_log_path=llm_log_path,
                    overlap_intervals_path=overlap_artifact_path,
                    retry_failed_size_cache=True,
                )
        srt_path = transcription_result.srt_path
        segments_json_path = transcription_result.segments_json_path
        if overlap_artifact_path and segments_json_path:
            try:
                speaker_mapping_summary = overlap_speaker_mapping.enrich_overlap_speaker_mapping_files(
                    overlap_path=overlap_artifact_path,
                    segments_path=segments_json_path,
                )
                if overlap_detection_metadata is not None:
                    overlap_detection_metadata["speaker_mapping"] = speaker_mapping_summary
            except Exception:
                logger.exception("Failed to enrich overlap speaker mapping for project %s", project_id)
        _update_progress(db, job_id, 30)

        # 4. Transcript overview (Pass 1)
        current_stage = "storyline"
        if resume_state:
            storyline_path = str(_podcast_cut_resume_storyline_path(temp_dir))
            logger.info("Reusing transcript overview for podcast-cut retry project %s", project_id)
        else:
            storyline_path = avid.transcript_overview(
                srt_path,
                output_path=str(output_dir / "storyline.json"),
                llm_log_path=str(llm_log_path),
            )
        _update_progress(db, job_id, 50)

        # 5. Cut (Pass 2)
        current_stage = "podcast_cut"
        cut_fn, cut_style_kwargs = _resolve_cut_runner(project)
        if project["cut_type"] in PODCAST_LIKE_CUT_TYPES:
            _write_podcast_cut_resume_state(
                temp_dir,
                project_id=project_id,
                project=project,
                source_sha256=source_sha256,
                srt_path=srt_path,
                storyline_path=storyline_path,
            )
        result_paths = cut_fn(
            source_path=source_path,
            srt_path=srt_path,
            segments_json_path=segments_json_path,
            context_path=storyline_path,
            output_dir=str(output_dir),
            final=True,
            extra_sources=extra_source_paths or None,
            edit_intensity=_output_edit_intensity(project),
            edit_decision_version=_output_edit_decision_version(project),
            segmentation_boundary_rule=_output_segmentation_boundary_rule(project),
            llm_log_path=str(llm_log_path),
            **cut_style_kwargs,
        )
        result_paths["storyline"] = storyline_path
        if segments_json_path:
            result_paths["segments_json"] = segments_json_path
        if overlap_artifact_path:
            result_paths["overlap_protection"] = str(overlap_artifact_path)
        _update_progress(db, job_id, 75)
        current_stage = "upload"

        # 5.5. Generate low-quality preview for review page
        import subprocess as sp
        preview_path = str(output_dir / "preview.mp4")
        try:
            sp.run([
                "ffmpeg", "-i", source_path,
                "-vf", "scale=-2:480",
                "-c:v", "libx264", "-preset", "fast", "-crf", "28",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                "-y", preview_path,
            ], check=True, timeout=600, capture_output=True)
            result_paths["preview"] = preview_path
        except Exception:
            logger.warning("Preview generation failed for project %s, skipping", project_id)

        if llm_log_path.exists() and llm_log_path.stat().st_size > 0:
            result_paths["llm_io_log"] = str(llm_log_path)

        # 6. Upload results to R2
        r2_keys = {}
        for key, local_path in result_paths.items():
            content_type = _guess_content_type(key)
            r2_key = f"results/{project_id}/{Path(local_path).name}"
            r2.upload_file(local_path, r2_key, content_type)
            r2_keys[key] = r2_key
        _update_progress(db, job_id, 85)

        # 7. Save edit report to DB
        if "report" in result_paths:
            report_text = Path(result_paths["report"]).read_text(encoding="utf-8")
            _save_report(db, project_id, duration, report_text)

        # 8. Confirm credit usage
        credit.confirm_usage(user_id, duration, job_id)

        # 9. Mark complete
        processing_metadata = _processing_metadata_with_overlap(
            transcription_result.processing_metadata,
            enabled=overlap_protection_enabled,
            detection_metadata=overlap_detection_metadata,
            chalna_metadata=transcription_result.metadata,
        )

        db.table("jobs").update({
            "status": "completed",
            "progress": 100,
            "result_r2_keys": r2_keys,
            "processing_metadata": processing_metadata,
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        db.table("projects").update({"status": "completed"}).eq("id", project_id).execute()

        # 10. Send email
        try:
            report = db.table("edit_reports").select("cut_percentage").eq("project_id", project_id).limit(1).execute()
            cut_pct = report.data[0]["cut_percentage"] if report.data else 0
            email.send_completion_email(user_email, project["name"], project_id, cut_pct)
        except Exception:
            logger.exception("Failed to send completion email for project %s", project_id)

    except Exception as e:
        logger.exception("Project %s failed", project_id)

        try:
            if credits_held and user_id:
                credit.release_hold(user_id, duration, job_id)
        except Exception:
            logger.exception("Failed to release credit hold for project %s", project_id)

        try:
            _mark_initial_project_failure(db, project_id, job_id=job_id, error_message=str(e))
        except Exception:
            logger.exception("Failed to mark project %s as failed after processing error", project_id)

        try:
            if user_email and project:
                email.send_failure_email(user_email, project["name"], project_id, str(e)[:200])
        except Exception:
            logger.exception("Failed to send failure email for project %s", project_id)

    finally:
        # Cleanup temp files unless a podcast-cut retry can reuse them.
        import shutil
        if _should_preserve_podcast_cut_temp(temp_dir, project, current_stage):
            logger.info("Preserving temp dir for podcast-cut retry: %s", temp_dir)
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _derive_project_sources(project_id: str, job_id: str | None) -> None:
    if not job_id:
        raise RuntimeError("source-derive job_id is required")

    db = get_db()
    claimed = (
        db.table("jobs")
        .update({
            "status": "running",
            "progress": 0,
            "error_message": None,
            "started_at": "now()",
            "completed_at": None,
        })
        .eq("id", job_id)
        .eq("project_id", project_id)
        .in_("status", ["queued", "pending"])
        .execute()
    )
    if not claimed.data:
        logger.info("Source-derive job %s for project %s was already claimed or finished", job_id, project_id)
        return

    job = db.table("jobs").select("input_payload").eq("id", job_id).single().execute().data
    input_payload = job.get("input_payload") or {}
    force = bool(input_payload.get("force"))
    project = db.table("projects").select("*").eq("id", project_id).single().execute().data
    source_keys = input_payload.get("source_keys") or source_derivatives.source_keys_needing_derivatives(project, force=force)
    source_keys = [str(key) for key in source_keys if key]
    if not source_keys:
        db.table("jobs").update({
            "status": "completed",
            "progress": 100,
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        return

    temp_root = settings.avid_temp_dir / "source_derivatives"
    temp_root.mkdir(parents=True, exist_ok=True)

    try:
        total = len(source_keys)
        for index, source_key in enumerate(source_keys):
            project = db.table("projects").select("*").eq("id", project_id).single().execute().data
            ref = source_derivatives.source_ref(project, source_key)
            if not force and source_derivatives.is_ready(ref.get("derived") or {}):
                _update_source_derive_progress(db, job_id, index + 1, total)
                continue

            project = _update_project_source_derivative(
                db,
                project_id=project_id,
                project=project,
                source_key=source_key,
                snapshot=source_derivatives.processing_snapshot(),
            )
            ref = source_derivatives.source_ref(project, source_key)
            try:
                snapshot, source_sha256 = source_derivatives.derive_r2_source(ref, temp_root)
                size_bytes = int(ref.get("size_bytes") or 0)
                duration_ms = snapshot.get("duration_ms")
                duration_seconds = int(round(int(duration_ms) / 1000)) if duration_ms else None
                source_derivatives.persist_asset_derivative(
                    db,
                    source_sha256=source_sha256,
                    size_bytes=size_bytes,
                    r2_key=ref["r2_key"],
                    filename=ref.get("filename"),
                    duration_seconds=duration_seconds,
                    snapshot=snapshot,
                )
                project = _update_project_source_derivative(
                    db,
                    project_id=project_id,
                    project=project,
                    source_key=source_key,
                    snapshot=snapshot,
                    source_sha256=source_sha256,
                )
            except Exception as exc:
                logger.exception("Failed to derive source %s for project %s", source_key, project_id)
                _update_project_source_derivative(
                    db,
                    project_id=project_id,
                    project=project,
                    source_key=source_key,
                    snapshot=source_derivatives.failed_snapshot(str(exc)),
                )
                raise
            _update_source_derive_progress(db, job_id, index + 1, total)

        db.table("jobs").update({
            "status": "completed",
            "progress": 100,
            "completed_at": "now()",
        }).eq("id", job_id).execute()
    except Exception as exc:
        db.table("jobs").update({
            "status": "failed",
            "error_message": str(exc)[:1000],
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        raise


def _derive_primary_source_best_effort(
    db,
    *,
    project_id: str,
    project: dict,
    source_path: Path,
    source_sha256: str,
) -> None:
    if source_derivatives.is_ready(project.get("source_derived") or {}):
        return
    try:
        snapshot, derived_sha256 = source_derivatives.derive_local_source(
            source_path=source_path,
            source_key="primary",
            source_r2_key=project["source_r2_key"],
            filename=project.get("source_filename"),
            size_bytes=project.get("source_size_bytes"),
        )
        if derived_sha256 != source_sha256:
            raise RuntimeError("derived source hash does not match registered source hash")
        size_bytes = int(project.get("source_size_bytes") or source_path.stat().st_size)
        source_derivatives.persist_asset_derivative(
            db,
            source_sha256=source_sha256,
            size_bytes=size_bytes,
            r2_key=project["source_r2_key"],
            filename=project.get("source_filename"),
            duration_seconds=project.get("source_duration_seconds"),
            snapshot=snapshot,
        )
        updated = _update_project_source_derivative(
            db,
            project_id=project_id,
            project=project,
            source_key="primary",
            snapshot=snapshot,
            source_sha256=source_sha256,
        )
        project.update(updated)
    except Exception:
        logger.exception("Best-effort primary source derivative generation failed for project %s", project_id)


def _ensure_chalna_audio_proxy(*, project: dict, source_path: Path, temp_dir: Path) -> Path:
    """Return the required 16 kHz mono FLAC input for Chalna."""
    source_path = Path(source_path)
    derived = project.get("source_derived") or {}
    if not source_derivatives.is_ready(derived):
        raise AudioProxyPreparationError("Primary audio proxy is not ready for Chalna transcription")

    _validate_chalna_audio_proxy_metadata(derived)

    audio_proxy_key = derived.get("audio_proxy_r2_key")
    if not isinstance(audio_proxy_key, str) or not audio_proxy_key:
        raise AudioProxyPreparationError("Primary audio proxy is missing its R2 key")
    if Path(audio_proxy_key).suffix.lower() != ".flac":
        raise AudioProxyPreparationError(f"Primary audio proxy R2 key must be FLAC: {audio_proxy_key}")

    proxy_path = _download_chalna_audio_proxy(
        audio_proxy_key=audio_proxy_key,
        source_path=source_path,
        temp_dir=Path(temp_dir),
    )
    _validate_chalna_audio_proxy_file(proxy_path, source_path=source_path)

    logger.info(
        "Using source audio proxy for Chalna transcription: project=%s proxy=%s size=%s",
        project.get("id"),
        audio_proxy_key,
        proxy_path.stat().st_size,
    )
    return proxy_path


def _validate_chalna_audio_proxy_metadata(derived: dict) -> None:
    expected = {
        "audio_codec": source_derivatives.AUDIO_PROXY_CODEC,
        "sample_rate": source_derivatives.AUDIO_PROXY_SAMPLE_RATE,
        "channels": source_derivatives.AUDIO_PROXY_CHANNELS,
    }
    actual = {
        "audio_codec": str(derived.get("audio_codec") or "").lower(),
        "sample_rate": _optional_int_value(derived.get("sample_rate")),
        "channels": _optional_int_value(derived.get("channels")),
    }
    if actual != expected:
        raise AudioProxyPreparationError(
            f"Primary audio proxy metadata is invalid: expected={expected} actual={actual}"
        )


def _download_chalna_audio_proxy(*, audio_proxy_key: str, source_path: Path, temp_dir: Path) -> Path:
    generated_proxy_path = source_path.parent / "audio_proxy.flac"
    if generated_proxy_path.exists() and generated_proxy_path.stat().st_size > 0:
        return generated_proxy_path

    proxy_path = temp_dir / "source.audio_proxy.flac"
    if proxy_path.exists() and proxy_path.stat().st_size > 0:
        return proxy_path

    r2.download_file(audio_proxy_key, str(proxy_path))
    return proxy_path


def _validate_chalna_audio_proxy_file(proxy_path: Path, *, source_path: Path) -> None:
    if proxy_path.resolve() == source_path.resolve():
        raise AudioProxyPreparationError("Refusing to submit the original source to Chalna")
    if proxy_path.suffix.lower() != ".flac":
        raise AudioProxyPreparationError(f"Chalna audio proxy must be FLAC: {proxy_path}")
    if not proxy_path.exists() or not proxy_path.is_file():
        raise AudioProxyPreparationError(f"Chalna audio proxy is missing: {proxy_path}")

    size_bytes = proxy_path.stat().st_size
    if size_bytes <= 0:
        raise AudioProxyPreparationError(f"Chalna audio proxy is empty: {proxy_path}")
    if size_bytes > CHALNA_MAX_INPUT_BYTES:
        raise AudioProxyPreparationError(
            f"Chalna audio proxy exceeds 2 GiB: size_bytes={size_bytes} path={proxy_path}"
        )


def _optional_int_value(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _update_source_derive_progress(db, job_id: str, completed: int, total: int) -> None:
    progress = min(100, max(0, round((completed / max(1, total)) * 100)))
    db.table("jobs").update({"progress": progress}).eq("id", job_id).execute()


def _update_project_source_derivative(
    db,
    *,
    project_id: str,
    project: dict,
    source_key: str,
    snapshot: dict,
    source_sha256: str | None = None,
) -> dict:
    updated_project = source_derivatives.set_project_source_snapshot(
        project,
        source_key,
        snapshot,
        source_sha256=source_sha256,
    )
    payload: dict[str, object] = {}
    if source_key == "primary":
        payload["source_derived"] = updated_project.get("source_derived") or {}
        if source_sha256:
            payload["source_sha256"] = source_sha256
    else:
        payload["extra_sources"] = updated_project.get("extra_sources") or []

    return db.table("projects").update(payload).eq("id", project_id).execute().data[0]


def _ensure_source_derivatives_current(
    db,
    *,
    project_id: str,
    project: dict,
    temp_root: Path,
) -> dict:
    source_keys = source_derivatives.source_keys_needing_derivatives(project)
    if not source_keys:
        return project

    temp_root.mkdir(parents=True, exist_ok=True)
    current_project = project
    for source_key in source_keys:
        current_project = _update_project_source_derivative(
            db,
            project_id=project_id,
            project=current_project,
            source_key=source_key,
            snapshot=source_derivatives.processing_snapshot(),
        )
        ref = source_derivatives.source_ref(current_project, source_key)
        snapshot, source_sha256 = source_derivatives.derive_r2_source(ref, temp_root)
        size_bytes = int(ref.get("size_bytes") or 0)
        duration_ms = snapshot.get("duration_ms")
        duration_seconds = int(round(int(duration_ms) / 1000)) if duration_ms else None
        source_derivatives.persist_asset_derivative(
            db,
            source_sha256=source_sha256,
            size_bytes=size_bytes,
            r2_key=ref["r2_key"],
            filename=ref.get("filename"),
            duration_seconds=duration_seconds,
            snapshot=snapshot,
        )
        current_project = _update_project_source_derivative(
            db,
            project_id=project_id,
            project=current_project,
            source_key=source_key,
            snapshot=snapshot,
            source_sha256=source_sha256,
        )
    return current_project


def _output_edit_intensity(project: dict) -> str:
    value = (project.get("settings") or {}).get("edit_intensity")
    return value if value in {"light", "normal", "heavy"} else "normal"


def _output_edit_decision_version(project: dict) -> str:
    value = (project.get("settings") or {}).get("edit_decision_version")
    return value if value in {"legacy", "boundary_aware_v1"} else "legacy"


def _output_segmentation_boundary_rule(project: dict) -> str:
    value = (project.get("settings") or {}).get("segmentation_boundary_rule")
    return (
        value
        if isinstance(value, str) and value in ALLOWED_SEGMENTATION_BOUNDARY_RULES
        else DEFAULT_SEGMENTATION_BOUNDARY_RULE
    )


def _processing_metadata_with_overlap(
    processing_metadata: dict,
    *,
    enabled: bool,
    detection_metadata: dict | None,
    chalna_metadata: dict | None,
) -> dict:
    metadata = dict(processing_metadata or {})
    if not enabled:
        return metadata

    detection = _compact_overlap_detection_metadata(detection_metadata or {})
    segmentation = None
    if isinstance(chalna_metadata, dict):
        overlap_summary = chalna_metadata.get("overlap_protection")
        if isinstance(overlap_summary, dict):
            segmentation = overlap_summary

    metadata["overlap_protection"] = {
        "enabled": True,
        "detection": detection,
        "segmentation": segmentation,
    }
    return metadata


def _compact_overlap_detection_metadata(payload: dict) -> dict:
    models: dict[str, dict] = {}
    for key, value in (payload.get("models") or {}).items():
        if not isinstance(value, dict):
            continue
        compact = {
            "status": value.get("status"),
            "model": value.get("model"),
            "intervals": value.get("intervals"),
            "total_overlap_ms": value.get("total_overlap_ms"),
            "elapsed_seconds": value.get("elapsed_seconds"),
        }
        if value.get("status") == "failed":
            compact["error_type"] = value.get("error_type")
            compact["error"] = value.get("error")
        models[str(key)] = compact
    compact_payload = {
        "schema_version": payload.get("schema_version"),
        "status": payload.get("status"),
        "interval_count": payload.get("interval_count"),
        "total_overlap_ms": payload.get("total_overlap_ms"),
        "elapsed_seconds": payload.get("elapsed_seconds"),
        "models": models,
    }
    speaker_mapping = payload.get("speaker_mapping")
    if isinstance(speaker_mapping, dict):
        compact_payload["speaker_mapping"] = {
            "schema_version": speaker_mapping.get("schema_version"),
            "method": speaker_mapping.get("method"),
            "intervals": speaker_mapping.get("intervals"),
            "mapped_intervals": speaker_mapping.get("mapped_intervals"),
            "segments": speaker_mapping.get("segments"),
            "enriched_segments": speaker_mapping.get("enriched_segments"),
        }
    return compact_payload


def _podcast_cut_resume_marker_path(temp_dir: Path) -> Path:
    return temp_dir / "output" / _PODCAST_CUT_RESUME_MARKER


def _podcast_cut_resume_srt_path(temp_dir: Path) -> Path:
    return temp_dir / "source.srt"


def _podcast_cut_resume_storyline_path(temp_dir: Path) -> Path:
    return temp_dir / "output" / "storyline.json"


def _project_settings_hash(project: dict) -> str:
    payload = json.dumps(
        project.get("settings") or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_valid_srt_file(path: Path) -> bool:
    if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        return "-->" in path.read_text(encoding="utf-8", errors="ignore")[:8192]
    except Exception:
        logger.exception("Failed to validate resume SRT %s", path)
        return False


def _is_valid_storyline_file(path: Path) -> bool:
    if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except Exception:
        logger.exception("Failed to validate resume storyline %s", path)
        return False


def _resume_state_source_matches_project(state: dict, project: dict) -> bool:
    expected_size = _int_or_none(state.get("source_size_bytes"))
    project_size = _int_or_none(project.get("source_size_bytes"))
    return (
        bool(state.get("source_sha256"))
        and state.get("source_sha256") == project.get("source_sha256")
        and expected_size is not None
        and project_size == expected_size
    )


def _local_source_matches_resume_state(path: Path, state: dict) -> bool:
    expected_size = _int_or_none(state.get("source_size_bytes"))
    expected_sha256 = state.get("source_sha256")
    if not path.exists() or not path.is_file() or expected_size is None or not expected_sha256:
        return False
    if path.stat().st_size != expected_size:
        return False
    return source_cache.sha256_file(path) == expected_sha256


def _load_podcast_cut_resume_state(temp_dir: Path, project: dict | None) -> dict | None:
    if not project or project.get("cut_type") not in PODCAST_LIKE_CUT_TYPES:
        return None

    marker_path = _podcast_cut_resume_marker_path(temp_dir)
    if not marker_path.exists():
        return None

    try:
        state = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load podcast-cut resume marker %s", marker_path)
        return None

    if not isinstance(state, dict):
        return None
    if state.get("failed_stage") != "podcast_cut":
        return None
    if state.get("project_id") != project.get("id"):
        return None
    if state.get("source_r2_key") != project.get("source_r2_key"):
        return None
    if state.get("cut_type") != project.get("cut_type"):
        return None
    if state.get("settings_hash") != _project_settings_hash(project):
        return None

    project_sha256 = project.get("source_sha256")
    if project_sha256 and state.get("source_sha256") != project_sha256:
        return None
    project_size = _int_or_none(project.get("source_size_bytes"))
    state_size = _int_or_none(state.get("source_size_bytes"))
    if project_size is not None and state_size != project_size:
        return None

    srt_path = _podcast_cut_resume_srt_path(temp_dir)
    storyline_path = _podcast_cut_resume_storyline_path(temp_dir)
    if state.get("srt_path") != str(srt_path):
        return None
    if state.get("storyline_path") != str(storyline_path):
        return None
    if not _is_valid_srt_file(srt_path):
        return None
    if not _is_valid_storyline_file(storyline_path):
        return None

    return state


def _write_podcast_cut_resume_state(
    temp_dir: Path,
    *,
    project_id: str,
    project: dict,
    source_sha256: str,
    srt_path: str,
    storyline_path: str,
) -> None:
    source_size_bytes = _int_or_none(project.get("source_size_bytes"))
    if not source_sha256 or source_size_bytes is None:
        logger.warning("Skipping podcast-cut resume marker for project %s: source identity missing", project_id)
        return

    marker_path = _podcast_cut_resume_marker_path(temp_dir)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "project_id": project_id,
        "source_r2_key": project.get("source_r2_key"),
        "source_sha256": source_sha256,
        "source_size_bytes": source_size_bytes,
        "cut_type": project.get("cut_type"),
        "settings_hash": _project_settings_hash(project),
        "srt_path": str(Path(srt_path)),
        "storyline_path": str(Path(storyline_path)),
        "failed_stage": "podcast_cut",
    }
    marker_path.write_text(
        json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )


def _podcast_cut_resume_transcription_result(
    srt_path: str,
    resume_state: dict,
) -> chalna.TranscriptionSrtResult:
    return chalna.TranscriptionSrtResult(
        srt_path=srt_path,
        external_task_id="",
        metadata={"segmentation_source": "resume"},
        segmentation_log=[],
        processing_metadata={
            "segmentation_source": "resume",
            "segmentation_mode": "podcast_cut_resume",
            "segmentation_label": "Podcast-cut retry resume",
            "fallback": False,
            "cache_hit": False,
            "cache_bypassed": False,
            "resumed_from_temp": True,
            "resume_failed_stage": resume_state.get("failed_stage"),
        },
    )


def _should_preserve_podcast_cut_temp(
    temp_dir: Path,
    project: dict | None,
    current_stage: str | None,
) -> bool:
    if current_stage != "podcast_cut":
        return False
    return _load_podcast_cut_resume_state(temp_dir, project) is not None


def _resolve_extra_source_offsets(extra_sources: list[dict]) -> list[int] | None:
    if not extra_sources:
        return None

    offsets = [item.get("offset_ms") for item in extra_sources]
    if not any(offset is not None for offset in offsets):
        return None
    if not all(offset is not None for offset in offsets):
        raise ValueError("manual offset 을 사용할 때는 모든 extra source 에 offset_ms 를 지정해야 합니다")
    return [int(offset) for offset in offsets]


def _local_extra_source_path(
    temp_dir: Path,
    extra_source: dict,
    index: int,
    used_names: set[str],
) -> Path:
    filename = Path(extra_source.get("filename") or f"extra_{index}.mp4").name
    if not filename:
        filename = f"extra_{index}.mp4"

    candidate = filename
    if candidate in used_names:
        stem = Path(filename).stem or f"extra_{index}"
        suffix = Path(filename).suffix
        candidate = f"{stem}_{index}{suffix}"

    used_names.add(candidate)
    return temp_dir / candidate


def _project_json_has_extra_sources(project_json_path: Path) -> bool:
    data = json.loads(project_json_path.read_text(encoding="utf-8"))
    return len(data.get("source_files") or []) > 1


class JobCanceled(RuntimeError):
    """Raised when a queued or running job has been canceled."""


def _extra_sources_hash(extra_sources: list[dict]) -> str | None:
    if not extra_sources:
        return None
    normalized = [
        {
            "r2_key": item.get("r2_key"),
            "filename": item.get("filename"),
            "size_bytes": item.get("size_bytes"),
            "offset_ms": item.get("offset_ms"),
        }
        for item in extra_sources
    ]
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_job_canceled(db, job_id: str) -> bool:
    row = execute_with_retry(
        lambda: db.table("jobs").select("status").eq("id", job_id).maybe_single().execute(),
        operation_name=f"jobs.select.cancel_status job_id={job_id}",
    )
    return bool(row.data and row.data.get("status") in {"cancel_requested", "canceled"})


def _raise_if_canceled(db, job_id: str) -> None:
    if _is_job_canceled(db, job_id):
        raise JobCanceled("작업 취소가 요청되었습니다")


def _update_multicam_state(db, project_id: str, **updates) -> None:
    project = execute_with_retry(
        lambda: db.table("projects").select("multicam_state").eq("id", project_id).maybe_single().execute(),
        operation_name=f"projects.select.multicam_state project_id={project_id}",
    )
    state = dict(project.data.get("multicam_state") or {}) if project.data else {}
    state.update(updates)
    execute_with_retry(
        lambda: db.table("projects").update({"multicam_state": state}).eq("id", project_id).execute(),
        operation_name=f"projects.update.multicam_state project_id={project_id}",
    )


def _multicam_settings_payload(project: dict) -> dict[str, object] | None:
    settings_value = project.get("settings") or {}
    if not isinstance(settings_value, dict):
        return None

    payload: dict[str, object] = {}
    switching = settings_value.get("multicam_switching")
    if switching in {"none", "follow_speaker", "conservative_follow_speaker"}:
        payload["switching"] = str(switching)

    audio_source_key = settings_value.get("audio_source_key")
    if isinstance(audio_source_key, str) and audio_source_key:
        payload["audio_source_key"] = audio_source_key

    speaker_source_map = settings_value.get("speaker_source_map")
    if isinstance(speaker_source_map, dict) and speaker_source_map:
        payload["speaker_source_map"] = {
            str(speaker): str(source_key)
            for speaker, source_key in speaker_source_map.items()
            if source_key is not None
        }

    return payload or None


def _multicam_export_options(project: dict, temp_dir: Path) -> dict[str, str]:
    payload = _multicam_settings_payload(project)
    if not payload:
        return {}

    options: dict[str, str] = {}
    switching = payload.get("switching")
    if isinstance(switching, str):
        options["multicam_switching"] = switching

    audio_source_key = payload.get("audio_source_key")
    if isinstance(audio_source_key, str):
        options["audio_source_key"] = audio_source_key

    speaker_source_map = payload.get("speaker_source_map")
    if isinstance(speaker_source_map, dict) and speaker_source_map:
        speaker_map_path = temp_dir / "speaker_source_map.json"
        speaker_map_path.write_text(
            json.dumps(speaker_source_map, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        options["speaker_source_map_path"] = str(speaker_map_path)

    return options


def _write_multicam_settings_to_project_json(project_json_path: Path, project: dict) -> None:
    payload = _multicam_settings_payload(project)
    if not payload:
        return

    data = json.loads(project_json_path.read_text(encoding="utf-8"))
    current = data.get("multicam_settings")
    data["multicam_settings"] = {
        **(current if isinstance(current, dict) else {}),
        **payload,
    }
    project_json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _plan_reprocess_steps(
    *,
    has_evaluation: bool,
    desired_extra_sources: bool,
    current_project_has_extra_sources: bool,
) -> list[str]:
    steps: list[str] = []
    if has_evaluation:
        steps.append("apply-evaluation")
    if desired_extra_sources:
        steps.append("rebuild-multicam")
    elif current_project_has_extra_sources:
        steps.append("clear-extra-sources")
    steps.append("export-project")
    return steps


def _reprocess_project(project_id: str, job_id: str | None) -> None:
    import shutil

    if not job_id:
        raise RuntimeError("reprocess job_id is required")

    db = get_db()
    project = execute_with_retry(
        lambda: db.table("projects").select("*").eq("id", project_id).single().execute(),
        operation_name=f"projects.select.reprocess project_id={project_id}",
    ).data
    user_id = project["user_id"]

    def cancel_check() -> bool:
        return _is_job_canceled(db, job_id)

    try:
        _raise_if_canceled(db, job_id)
    except JobCanceled:
        execute_with_retry(
            lambda: db.table("jobs").update({
                "status": "canceled",
                "progress": 0,
                "completed_at": "now()",
            }).eq("id", job_id).execute(),
            operation_name=f"jobs.update.canceled job_id={job_id}",
        )
        execute_with_retry(
            lambda: db.table("projects").update({"status": "completed"}).eq("id", project_id).execute(),
            operation_name=f"projects.update.completed project_id={project_id}",
        )
        _update_multicam_state(db, project_id, status="canceled", job_id=job_id)
        return

    claimed = execute_with_retry(
        lambda: (
            db.table("jobs")
            .update({
                "status": "running",
                "progress": 5,
                "error_message": None,
                "started_at": "now()",
                "completed_at": None,
            })
            .eq("id", job_id)
            .eq("project_id", project_id)
            .in_("status", ["queued", "pending"])
            .execute()
        ),
        operation_name=f"jobs.claim.reprocess job_id={job_id} project_id={project_id}",
    )
    if not claimed.data:
        logger.info("Reprocess job %s for project %s was already claimed or finished", job_id, project_id)
        return

    temp_dir = settings.avid_temp_dir / f"multicam_{project_id}"
    try:
        execute_with_retry(
            lambda: db.table("projects").update({"status": "processing"}).eq("id", project_id).execute(),
            operation_name=f"projects.update.processing project_id={project_id}",
        )
        _update_multicam_state(db, project_id, status="running", job_id=job_id, error=None)

        _raise_if_canceled(db, job_id)
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_dir = temp_dir / "output"
        output_dir.mkdir(exist_ok=True)

        completed_job = get_latest_artifact_job(db, project_id, select="id, result_r2_keys")
        if not completed_job:
            raise RuntimeError("완료된 작업이 없습니다. 전체 재처리가 필요합니다.")

        r2_keys = dict(completed_job["result_r2_keys"])
        project_json_key = r2_keys.get("project_json")
        if not project_json_key:
            raise RuntimeError("프로젝트 JSON이 없습니다. 전체 재처리가 필요합니다.")

        _raise_if_canceled(db, job_id)
        project_json_bytes = r2.download_to_bytes(project_json_key)
        _raise_if_canceled(db, job_id)
        local_project_json = temp_dir / "input.project.avid.json"
        local_project_json.write_bytes(project_json_bytes)
        working_project_json = local_project_json

        stored_project_json = json.loads(project_json_bytes.decode("utf-8"))
        current_project_has_extra_sources = len(stored_project_json.get("source_files") or []) > 1

        eval_result = execute_with_retry(
            lambda: (
                db.table("evaluations")
                .select("segments")
                .eq("project_id", project_id)
                .eq("evaluator_id", user_id)
                .limit(1)
                .execute()
            ),
            operation_name=f"evaluations.select.segments project_id={project_id}",
        )
        evaluation_payload = eval_result.data[0]["segments"] if eval_result.data else None
        if isinstance(evaluation_payload, dict):
            eval_segments = evaluation_payload.get("segments") or []
        else:
            eval_segments = evaluation_payload

        has_extra_sources = bool(project.get("extra_sources"))

        if not eval_segments and not has_extra_sources and not current_project_has_extra_sources:
            raise RuntimeError("평가 데이터 또는 적용할 extra source 변경이 필요합니다")

        evaluation_path = None
        if eval_segments:
            evaluation_path = temp_dir / "evaluation.json"
            serialized_evaluation = (
                evaluation_payload
                if isinstance(evaluation_payload, dict)
                else {"segments": eval_segments}
            )
            evaluation_path.write_text(
                json.dumps(serialized_evaluation, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        source_manifest_path = None
        if has_extra_sources:
            _raise_if_canceled(db, job_id)
            project = _ensure_source_derivatives_current(
                db,
                project_id=project_id,
                project=project,
                temp_root=settings.avid_temp_dir / "source_derivatives",
            )
            _raise_if_canceled(db, job_id)
            local_derivatives = source_derivatives.download_ready_derivatives(
                project,
                temp_dir / "derived_sources",
            )
            _raise_if_canceled(db, job_id)
            source_manifest_path = source_derivatives.build_manifest(
                project,
                temp_dir / "multicam_sources.json",
                local_derivatives,
            )

        steps = _plan_reprocess_steps(
            has_evaluation=bool(eval_segments),
            desired_extra_sources=has_extra_sources,
            current_project_has_extra_sources=_project_json_has_extra_sources(working_project_json),
        )

        execute_with_retry(
            lambda: db.table("jobs").update({"progress": 25}).eq("id", job_id).execute(),
            operation_name=f"jobs.update.progress25 job_id={job_id}",
        )
        sync_diagnostics_path = None

        if "apply-evaluation" in steps:
            eval_output = temp_dir / "01_eval_applied.project.avid.json"
            _raise_if_canceled(db, job_id)
            payload = avid.apply_evaluation(
                project_json_path=str(working_project_json),
                evaluation_path=str(evaluation_path),
                output_project_json=str(eval_output),
                is_canceled=cancel_check,
            )
            _raise_if_canceled(db, job_id)
            working_project_json = Path(payload["artifacts"]["project_json"])
            logger.info("Applied evaluation via avid-cli: %s", payload)

        if "rebuild-multicam" in steps:
            multicam_output = temp_dir / "02_multicam.project.avid.json"
            _raise_if_canceled(db, job_id)
            if not source_manifest_path:
                raise RuntimeError("멀티캠 source manifest가 준비되지 않았습니다")
            payload = avid.rebuild_multicam_from_manifest(
                project_json_path=str(working_project_json),
                source_manifest_path=str(source_manifest_path),
                output_project_json=str(multicam_output),
                is_canceled=cancel_check,
            )
            _raise_if_canceled(db, job_id)
            working_project_json = Path(payload["artifacts"]["project_json"])
            sync_diagnostics_path = (payload.get("artifacts") or {}).get("sync_diagnostics")
            logger.info("Rebuilt multicam via avid-cli: %s", payload)
        elif "clear-extra-sources" in steps:
            clear_output = temp_dir / "02_cleared.project.avid.json"
            _raise_if_canceled(db, job_id)
            payload = avid.clear_extra_sources(
                project_json_path=str(working_project_json),
                output_project_json=str(clear_output),
                is_canceled=cancel_check,
            )
            _raise_if_canceled(db, job_id)
            working_project_json = Path(payload["artifacts"]["project_json"])
            logger.info("Cleared extra sources via avid-cli: %s", payload)

        execute_with_retry(
            lambda: db.table("jobs").update({"progress": 70}).eq("id", job_id).execute(),
            operation_name=f"jobs.update.progress70 job_id={job_id}",
        )

        _raise_if_canceled(db, job_id)
        _write_multicam_settings_to_project_json(working_project_json, project)
        payload = avid.export_project(
            project_json_path=str(working_project_json),
            output_dir=str(output_dir),
            content_mode="cut",
            is_canceled=cancel_check,
            **_multicam_export_options(project, temp_dir),
        )
        _raise_if_canceled(db, job_id)
        artifacts = payload.get("artifacts") or {}
        updated_json = working_project_json
        fcpxml_path = Path(artifacts["fcpxml"])
        srt_path = Path(artifacts["srt"]) if artifacts.get("srt") else None

        new_r2_keys = dict(r2_keys)

        pj_r2_key = f"results/{project_id}/{updated_json.name}"
        _raise_if_canceled(db, job_id)
        r2.upload_file(str(updated_json), pj_r2_key, "application/json")
        new_r2_keys["project_json"] = pj_r2_key

        fcpxml_r2_key = f"results/{project_id}/{fcpxml_path.name}"
        _raise_if_canceled(db, job_id)
        r2.upload_file(str(fcpxml_path), fcpxml_r2_key, "application/xml")
        new_r2_keys["fcpxml"] = fcpxml_r2_key

        if srt_path:
            srt_r2_key = f"results/{project_id}/{srt_path.name}"
            _raise_if_canceled(db, job_id)
            r2.upload_file(str(srt_path), srt_r2_key, "text/plain")
            new_r2_keys["srt"] = srt_r2_key

        if sync_diagnostics_path:
            sync_path = Path(sync_diagnostics_path)
            sync_r2_key = f"results/{project_id}/{sync_path.name}"
            _raise_if_canceled(db, job_id)
            r2.upload_file(str(sync_path), sync_r2_key, "application/json")
            new_r2_keys["sync_diagnostics"] = sync_r2_key
        elif "sync_diagnostics" in new_r2_keys:
            new_r2_keys.pop("sync_diagnostics", None)

        execute_with_retry(
            lambda: db.table("jobs").update({
                "status": "completed",
                "progress": 100,
                "result_r2_keys": new_r2_keys,
                "completed_at": "now()",
            }).eq("id", job_id).execute(),
            operation_name=f"jobs.update.completed job_id={job_id}",
        )
        applied_hash = _extra_sources_hash(project.get("extra_sources") or [])
        execute_with_retry(
            lambda: db.table("projects").update({
                "status": "completed",
                "multicam_state": {
                    **(project.get("multicam_state") or {}),
                    "status": "applied" if has_extra_sources else "not_applied",
                    "desired_sources_hash": applied_hash,
                    "applied_sources_hash": applied_hash,
                    "source_count": len(project.get("extra_sources") or []),
                    "job_id": job_id,
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                    "error": None,
                },
            }).eq("id", project_id).execute(),
            operation_name=f"projects.update.reprocess_completed project_id={project_id}",
        )
        logger.info("Reprocess completed for project %s", project_id)
    except (JobCanceled, avid.AvidCommandCanceled):
        logger.info("Project reprocess canceled for project %s", project_id)
        try:
            execute_with_retry(
                lambda: db.table("jobs").update({
                    "status": "canceled",
                    "completed_at": "now()",
                }).eq("id", job_id).execute(),
                operation_name=f"jobs.update.reprocess_canceled job_id={job_id}",
            )
            execute_with_retry(
                lambda: db.table("projects").update({"status": "completed"}).eq("id", project_id).execute(),
                operation_name=f"projects.update.reprocess_canceled project_id={project_id}",
            )
            _update_multicam_state(db, project_id, status="canceled", job_id=job_id, error=None)
        except Exception:
            logger.exception("Failed to persist reprocess cancellation state for project %s job %s", project_id, job_id)
    except Exception as exc:
        logger.exception("Project reprocess failed for project %s", project_id)
        error_message = str(exc)[:1000]
        try:
            execute_with_retry(
                lambda: db.table("jobs").update({
                    "status": "failed",
                    "error_message": error_message,
                    "completed_at": "now()",
                }).eq("id", job_id).execute(),
                operation_name=f"jobs.update.reprocess_failed job_id={job_id}",
            )
            execute_with_retry(
                lambda: db.table("projects").update({"status": "reprocess_failed"}).eq("id", project_id).execute(),
                operation_name=f"projects.update.reprocess_failed project_id={project_id}",
            )
            _update_multicam_state(db, project_id, status="failed", job_id=job_id, error=error_message)
        except Exception:
            logger.exception("Failed to persist reprocess failure state for project %s job %s", project_id, job_id)
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _format_srt_timestamp(ms: int) -> str:
    ms = max(0, int(ms))
    hours = ms // 3_600_000
    minutes = (ms % 3_600_000) // 60_000
    seconds = (ms % 60_000) // 1000
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _write_transcription_srt_from_project_json(project_json_path: Path, output_path: Path) -> None:
    project_data = json.loads(project_json_path.read_text(encoding="utf-8"))
    transcription = project_data.get("transcription") or {}
    segments = transcription.get("segments") or []

    lines: list[str] = []
    cue_index = 1
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        try:
            start_ms = int(round(float(segment.get("start_ms"))))
            end_ms = int(round(float(segment.get("end_ms"))))
        except (TypeError, ValueError):
            continue
        if end_ms <= start_ms:
            continue

        lines.extend([
            str(cue_index),
            f"{_format_srt_timestamp(start_ms)} --> {_format_srt_timestamp(end_ms)}",
            text,
            "",
        ])
        cue_index += 1

    if not lines:
        raise RuntimeError("프로젝트 JSON에 재사용할 transcription segment가 없습니다")
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _cut_decision_project(project_id: str, job_id: str | None) -> None:
    import shutil

    if not job_id:
        raise RuntimeError("cut decision job_id is required")

    db = get_db()
    temp_dir = settings.avid_temp_dir / f"cut_decision_{project_id}_{job_id[:8]}"
    previous_project_status = "completed"

    try:
        claimed = (
            db.table("jobs")
            .update({
                "status": "running",
                "progress": 0,
                "error_message": None,
                "started_at": "now()",
                "completed_at": None,
                "pipeline_stages": _cut_decision_pipeline_stages("reuse_segments", 1),
            })
            .eq("id", job_id)
            .eq("project_id", project_id)
            .in_("status", ["queued", "pending"])
            .execute()
        )
        if not claimed.data:
            logger.info("Cut decision job %s for project %s was already claimed or finished", job_id, project_id)
            return

        project = db.table("projects").select("*").eq("id", project_id).single().execute().data
        if not project:
            raise RuntimeError("프로젝트를 찾을 수 없습니다")
        previous_project_status = str(project.get("status") or "completed")
        db.table("projects").update({"status": "processing"}).eq("id", project_id).execute()

        temp_dir.mkdir(parents=True, exist_ok=True)
        output_dir = temp_dir / "output"
        output_dir.mkdir(exist_ok=True)

        completed_job = get_latest_artifact_job(db, project_id, select="id, result_r2_keys, type, created_at")
        if not completed_job:
            raise RuntimeError("완료된 기준 산출물이 없습니다")

        base_r2_keys = dict(completed_job["result_r2_keys"])
        project_json_key = base_r2_keys.get("project_json")
        if not project_json_key:
            raise RuntimeError("프로젝트 JSON이 없어 cut decision만 다시 실행할 수 없습니다")

        local_project_json = temp_dir / "input.project.avid.json"
        r2.download_file(project_json_key, str(local_project_json))
        srt_path = temp_dir / "source.refined.srt"
        _write_transcription_srt_from_project_json(local_project_json, srt_path)

        storyline_path: Path | None = None
        storyline_key = base_r2_keys.get("storyline")
        if storyline_key:
            storyline_path = temp_dir / "storyline.json"
            r2.download_file(storyline_key, str(storyline_path))

        source_r2_key = project.get("source_r2_key")
        if not source_r2_key:
            raise RuntimeError("원본 소스 정보가 없어 cut decision을 다시 실행할 수 없습니다")
        source_ext = Path(project.get("source_filename") or "source.mp4").suffix or ".mp4"
        source_path = temp_dir / f"source{source_ext}"
        r2.download_file(source_r2_key, str(source_path))

        extra_source_paths: list[str] = []
        used_extra_names: set[str] = set()
        for i, extra_source in enumerate(project.get("extra_sources") or []):
            local_extra_path = _local_extra_source_path(temp_dir, extra_source, i, used_extra_names)
            r2.download_file(extra_source["r2_key"], str(local_extra_path))
            extra_source_paths.append(str(local_extra_path))

        _update_cut_decision_progress(db, job_id, 25, "edit_decision", 1)

        llm_log_path = output_dir / "llm_io.jsonl"
        cut_fn, cut_style_kwargs = _resolve_cut_runner(project)
        result_paths = cut_fn(
            source_path=str(source_path),
            srt_path=str(srt_path),
            context_path=str(storyline_path) if storyline_path else None,
            output_dir=str(output_dir),
            final=True,
            extra_sources=extra_source_paths or None,
            edit_intensity=_output_edit_intensity(project),
            edit_decision_version=_output_edit_decision_version(project),
            llm_log_path=str(llm_log_path),
            **cut_style_kwargs,
        )
        if llm_log_path.exists() and llm_log_path.stat().st_size > 0:
            result_paths["llm_io_log"] = str(llm_log_path)

        _update_cut_decision_progress(db, job_id, 75, "upload_results", 1)

        new_r2_keys = dict(base_r2_keys)
        for key, local_path in result_paths.items():
            content_type = _guess_content_type(key)
            r2_key = f"results/{project_id}/cut_decision_{job_id[:8]}_{Path(local_path).name}"
            r2.upload_file(local_path, r2_key, content_type)
            new_r2_keys[key] = r2_key
        if "sync_diagnostics" not in result_paths:
            new_r2_keys.pop("sync_diagnostics", None)

        if "report" in result_paths:
            report_text = Path(result_paths["report"]).read_text(encoding="utf-8")
            db.table("edit_reports").delete().eq("project_id", project_id).execute()
            _save_report(db, project_id, int(project.get("source_duration_seconds") or 0), report_text)

        db.table("jobs").update({
            "status": "completed",
            "progress": 100,
            "pipeline_stages": _cut_decision_pipeline_stages(completed=True),
            "result_r2_keys": new_r2_keys,
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        db.table("projects").update({"status": "completed"}).eq("id", project_id).execute()
        logger.info("Cut decision rerun completed for project %s", project_id)
    except Exception as exc:
        logger.exception("Cut decision rerun failed for project %s", project_id)
        db.table("jobs").update({
            "status": "failed",
            "error_message": str(exc)[:1000],
            "pipeline_stages": _cut_decision_pipeline_stages("edit_decision", failed=True),
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        db.table("projects").update({"status": previous_project_status}).eq("id", project_id).execute()
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _fcpxml_time_seconds(value: str | None) -> float:
    if not value:
        return 0.0
    raw = value[:-1] if value.endswith("s") else value
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        return float(Fraction(int(numerator), int(denominator)))
    return float(raw)


def _multicam_angle_offsets(root: ET.Element) -> dict[str, dict[str, float]]:
    offsets_by_media: dict[str, dict[str, float]] = {}
    for media in root.findall("./resources/media"):
        media_id = media.get("id")
        multicam = media.find("multicam")
        if not media_id or multicam is None:
            continue

        angle_offsets: dict[str, float] = {}
        for angle in multicam.findall("mc-angle"):
            angle_id = angle.get("angleID")
            angle_clip = angle.find("asset-clip")
            if not angle_id or angle_clip is None:
                continue
            angle_offsets[angle_id] = _fcpxml_time_seconds(angle_clip.get("offset"))
        offsets_by_media[media_id] = angle_offsets
    return offsets_by_media


def _source_start_seconds_from_clip(
    clip: ET.Element,
    multicam_offsets: dict[str, dict[str, float]],
) -> float:
    start = _fcpxml_time_seconds(clip.get("start"))
    if clip.tag != "mc-clip":
        return start

    mc_source = clip.find("mc-source")
    angle_id = mc_source.get("angleID") if mc_source is not None else None
    if not angle_id:
        return start

    angle_offsets = multicam_offsets.get(clip.get("ref") or "", {})
    return max(0.0, start - angle_offsets.get(angle_id, 0.0))


def _primary_intervals_from_fcpxml(fcpxml_path: Path) -> list[tuple[float, float]]:
    root = ET.parse(fcpxml_path).getroot()
    spine = root.find("./library/event/project/sequence/spine")
    if spine is None:
        return []

    multicam_offsets = _multicam_angle_offsets(root)
    intervals: list[tuple[float, float]] = []
    for clip in list(spine):
        if clip.tag not in {"asset-clip", "mc-clip"} or clip.get("lane") is not None:
            continue
        if clip.get("enabled") == "0":
            continue
        start = _source_start_seconds_from_clip(clip, multicam_offsets)
        duration = _fcpxml_time_seconds(clip.get("duration"))
        if duration > 0:
            intervals.append((start, duration))
    return intervals


def _int_value(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _merge_time_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    valid_ranges = sorted((start, end) for start, end in ranges if end > start)
    if not valid_ranges:
        return []

    merged: list[tuple[int, int]] = []
    current_start, current_end = valid_ranges[0]
    for start, end in valid_ranges[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        merged.append((current_start, current_end))
        current_start, current_end = start, end
    merged.append((current_start, current_end))
    return merged


def _subtract_time_ranges(
    start_ms: int,
    end_ms: int,
    protected_ranges: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    pieces: list[tuple[int, int]] = []
    cursor = start_ms
    for protected_start, protected_end in protected_ranges:
        if protected_end <= cursor:
            continue
        if protected_start >= end_ms:
            break
        if protected_start > cursor:
            pieces.append((cursor, min(protected_start, end_ms)))
        cursor = max(cursor, protected_end)
        if cursor >= end_ms:
            break
    if cursor < end_ms:
        pieces.append((cursor, end_ms))
    return pieces


def _range_overlaps_any(start_ms: int, end_ms: int, ranges: list[tuple[int, int]]) -> bool:
    return any(range_start < end_ms and start_ms < range_end for range_start, range_end in ranges)


def _primary_video_track(project_data: dict) -> dict | None:
    for track in project_data.get("tracks") or []:
        if track.get("track_type") == "video":
            return track
    return None


def _source_duration_ms(project_data: dict, primary_track: dict | None) -> int:
    source_file_id = primary_track.get("source_file_id") if primary_track else None
    duration_candidates: list[int] = []

    for source_file in project_data.get("source_files") or []:
        info = source_file.get("info") or {}
        duration_ms = _int_value(info.get("duration_ms"))
        if duration_ms and duration_ms > 0:
            duration_candidates.append(duration_ms)
            if source_file_id and source_file.get("id") == source_file_id:
                return duration_ms

    transcription = project_data.get("transcription") or {}
    for segment in transcription.get("segments") or []:
        end_ms = _int_value(segment.get("end_ms"))
        if end_ms and end_ms > 0:
            duration_candidates.append(end_ms)

    for decision in project_data.get("edit_decisions") or []:
        range_data = decision.get("range") or {}
        end_ms = _int_value(range_data.get("end_ms"))
        if end_ms and end_ms > 0:
            duration_candidates.append(end_ms)

    return max(duration_candidates, default=0)


def _review_segment_ranges(project_data: dict) -> list[tuple[int, int, int, str | None]]:
    transcription = project_data.get("transcription") or {}
    raw_segments = transcription.get("segments") or []
    valid_segments: list[tuple[int, int, int, int, str | None]] = []

    for position, segment in enumerate(raw_segments):
        start_ms = _int_value(segment.get("start_ms"))
        end_ms = _int_value(segment.get("end_ms"))
        if start_ms is None or end_ms is None or end_ms <= start_ms:
            continue
        segment_index = _int_value(segment.get("index"))
        if segment_index is None:
            segment_index = position + 1
        speaker = segment.get("speaker")
        valid_segments.append((
            position,
            segment_index,
            start_ms,
            end_ms,
            str(speaker) if speaker else None,
        ))

    if not valid_segments:
        return []

    if project_data.get("segmentation_boundary_rule") != "word_boundary":
        return [
            (segment_index, start_ms, end_ms, speaker)
            for _, segment_index, start_ms, end_ms, speaker in valid_segments
        ]

    starts = {position: start_ms for position, _, start_ms, _, _ in valid_segments}
    ends = {position: end_ms for position, _, _, end_ms, _ in valid_segments}

    for current, following in zip(valid_segments, valid_segments[1:]):
        current_position, _, _, current_end, _ = current
        next_position, _, next_start, _, _ = following
        boundary = (current_end + next_start) // 2
        ends[current_position] = boundary
        starts[next_position] = boundary

    ranges: list[tuple[int, int, int, str | None]] = []
    for position, segment_index, raw_start, raw_end, speaker in valid_segments:
        start_ms = starts[position]
        end_ms = ends[position]
        if end_ms <= start_ms:
            start_ms = raw_start
            end_ms = raw_end
        ranges.append((segment_index, start_ms, end_ms, speaker))
    return ranges


def _final_preview_removed_ranges(
    project_data: dict,
    primary_track_id: str,
    review_ranges: list[tuple[int, int, int, str | None]],
) -> list[tuple[int, int]]:
    ranges_by_index = {
        segment_index: (start_ms, end_ms)
        for segment_index, start_ms, end_ms, _speaker in review_ranges
    }
    protected_ranges = _merge_time_ranges([
        (start_ms, end_ms)
        for _segment_index, start_ms, end_ms, _speaker in review_ranges
    ])

    removed_ranges: list[tuple[int, int]] = []
    for decision in project_data.get("edit_decisions") or []:
        if decision.get("active_video_track_id") != primary_track_id:
            continue
        if decision.get("edit_type") not in {"cut", "mute"}:
            continue

        range_data = decision.get("range") or {}
        start_ms = _int_value(range_data.get("start_ms"))
        end_ms = _int_value(range_data.get("end_ms"))
        if start_ms is None or end_ms is None or end_ms <= start_ms:
            continue

        if decision.get("reason") == "silence":
            removed_ranges.extend(_subtract_time_ranges(start_ms, end_ms, protected_ranges))
            continue

        source_segment_index = _int_value(decision.get("source_segment_index"))
        review_range = ranges_by_index.get(source_segment_index) if source_segment_index is not None else None
        if review_range is not None:
            removed_ranges.append(review_range)
        else:
            removed_ranges.append((start_ms, end_ms))

    return _merge_time_ranges(removed_ranges)


def _merge_adjacent_final_preview_segments(
    segments: list[tuple[int, int, int, str, str | None]],
) -> list[tuple[int, int, str]]:
    merged: list[tuple[int, int, int, str, str | None]] = []
    for segment in segments:
        segment_index, start_ms, end_ms, state, speaker = segment
        if not merged:
            merged.append(segment)
            continue

        prev_index, prev_start, prev_end, prev_state, prev_speaker = merged[-1]
        gap_ms = max(0, start_ms - prev_end)
        if (
            prev_state == state == "enabled"
            and prev_speaker is not None
            and prev_speaker == speaker
            and gap_ms < FINAL_PREVIEW_MERGE_GAP_MS
        ):
            merged[-1] = (prev_index, prev_start, end_ms, prev_state, prev_speaker)
            continue
        merged.append((segment_index, start_ms, end_ms, state, speaker))

    return [(start_ms, end_ms, state) for _index, start_ms, end_ms, state, _speaker in merged]


def _invert_removed_ranges(
    removed_ranges: list[tuple[int, int]],
    total_duration_ms: int,
) -> list[tuple[int, int]]:
    keep_ranges: list[tuple[int, int]] = []
    cursor = 0
    for start_ms, end_ms in _merge_time_ranges(removed_ranges):
        start_ms = max(0, min(start_ms, total_duration_ms))
        end_ms = max(0, min(end_ms, total_duration_ms))
        if end_ms <= start_ms:
            continue
        if start_ms > cursor:
            keep_ranges.append((cursor, start_ms))
        cursor = max(cursor, end_ms)
    if cursor < total_duration_ms:
        keep_ranges.append((cursor, total_duration_ms))
    return keep_ranges


def _final_preview_intervals_from_project_json(project_json_path: Path) -> list[tuple[float, float]]:
    project_data = json.loads(project_json_path.read_text(encoding="utf-8"))
    primary_track = _primary_video_track(project_data)
    if not primary_track:
        return []

    primary_track_id = str(primary_track.get("id") or "")
    if not primary_track_id:
        return []

    total_duration_ms = _source_duration_ms(project_data, primary_track)
    review_ranges = _review_segment_ranges(project_data)
    removed_ranges = _final_preview_removed_ranges(project_data, primary_track_id, review_ranges)

    if review_ranges:
        review_segments: list[tuple[int, int, int, str, str | None]] = []
        for segment_index, start_ms, end_ms, speaker in review_ranges:
            if total_duration_ms > 0:
                start_ms = max(0, min(start_ms, total_duration_ms))
                end_ms = max(0, min(end_ms, total_duration_ms))
            if end_ms <= start_ms:
                continue
            state = "removed" if _range_overlaps_any(start_ms, end_ms, removed_ranges) else "enabled"
            review_segments.append((segment_index, start_ms, end_ms, state, speaker))

        keep_ranges = [
            (start_ms, end_ms)
            for start_ms, end_ms, state in _merge_adjacent_final_preview_segments(review_segments)
            if state != "removed"
        ]
    else:
        keep_ranges = _invert_removed_ranges(removed_ranges, total_duration_ms)

    return [
        (start_ms / 1000.0, (end_ms - start_ms) / 1000.0)
        for start_ms, end_ms in keep_ranges
        if end_ms > start_ms
    ]


def _has_audio_stream(source_path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(source_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _probe_duration_ms(path: Path) -> int:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe duration failed for {path}: {result.stderr[-500:]}")
    return int(round(float(result.stdout.strip()) * 1000))


def _render_intervals(source_path: Path, intervals: list[tuple[float, float]], output_path: Path) -> dict:
    """Render keep intervals without building one large ffmpeg filter graph.

    A single trim/atrim/concat graph keeps many decoded streams alive at once and
    can consume tens of GB for ordinary review timelines. Render each interval
    independently, then concatenate the normalized segment files.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    segment_dir = output_path.parent / f"{output_path.stem}_segments"
    segment_dir.mkdir(parents=True, exist_ok=True)
    has_audio = _has_audio_stream(source_path)

    segment_paths: list[Path] = []
    manifest_intervals: list[dict] = []
    preview_cursor_ms = 0
    for index, (start, duration) in enumerate(intervals):
        if duration <= 0:
            continue

        segment_path = segment_dir / f"segment_{index:04d}.mp4"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-ss",
            f"{max(0.0, start):.6f}",
            "-i",
            str(source_path),
            "-t",
            f"{duration:.6f}",
            "-map",
            "0:v:0",
        ]
        if has_audio:
            cmd += ["-map", "0:a:0"]
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
        ]
        if has_audio:
            cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]
        else:
            cmd += ["-an"]
        cmd += ["-movflags", "+faststart", str(segment_path)]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if result.returncode != 0:
            raise RuntimeError(
                f"final preview segment render failed at interval {index + 1}/{len(intervals)} "
                f"(start={start:.3f}s, duration={duration:.3f}s): {result.stderr[-1000:]}"
            )
        actual_duration_ms = _probe_duration_ms(segment_path)
        source_start_ms = int(round(start * 1000))
        requested_duration_ms = int(round(duration * 1000))
        source_end_ms = source_start_ms + requested_duration_ms
        manifest_intervals.append({
            "source_start_ms": source_start_ms,
            "source_end_ms": source_end_ms,
            "requested_duration_ms": requested_duration_ms,
            "actual_duration_ms": actual_duration_ms,
            "preview_start_ms": preview_cursor_ms,
            "preview_end_ms": preview_cursor_ms + actual_duration_ms,
        })
        preview_cursor_ms += actual_duration_ms
        segment_paths.append(segment_path)

    if not segment_paths:
        raise RuntimeError("미리보기로 렌더링할 keep 구간이 없습니다")

    concat_list = output_path.with_suffix(".concat.txt")
    concat_list.write_text(
        "\n".join(f"file '{str(path).replace(chr(39), chr(92) + chr(39))}'" for path in segment_paths),
        encoding="utf-8",
    )

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if result.returncode != 0:
        raise RuntimeError(f"final preview concat failed: {result.stderr[-1000:]}")
    return {"version": 1, "intervals": manifest_intervals}


def _burn_subtitles(input_path: Path, srt_path: Path | None, output_path: Path) -> None:
    import shutil

    if not srt_path or not srt_path.exists() or not srt_path.read_text(encoding="utf-8").strip():
        shutil.copyfile(input_path, output_path)
        return

    subtitle_filter = f"subtitles={srt_path}:charenc=UTF-8"
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            subtitle_filter,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "26",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=7200,
    )
    if result.returncode != 0:
        raise RuntimeError(f"subtitle burn-in failed: {result.stderr[-1000:]}")


def _write_webvtt_from_srt(srt_path: Path | None, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not srt_path or not srt_path.exists() or not srt_path.read_text(encoding="utf-8").strip():
        output_path.write_text("WEBVTT\n\n", encoding="utf-8")
        return

    lines = ["WEBVTT", ""]
    for line in srt_path.read_text(encoding="utf-8").splitlines():
        lines.append(line.replace(",", ".") if "-->" in line else line)
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _format_webvtt_time(ms: int | float) -> str:
    total_ms = max(0, int(round(ms)))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    seconds = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _write_final_preview_webvtt_from_source_segments(
    applied_project_json: Path,
    render_manifest: dict,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    project_data = json.loads(applied_project_json.read_text(encoding="utf-8"))
    transcription = project_data.get("transcription") or {}
    source_segments = transcription.get("segments") or []
    render_intervals = render_manifest.get("intervals") or []

    lines = ["WEBVTT", ""]
    cue_index = 1
    for segment in source_segments:
        try:
            cue_start_ms = int(segment.get("start_ms"))
            cue_end_ms = int(segment.get("end_ms"))
        except (TypeError, ValueError):
            continue
        text = str(segment.get("text") or "").strip()
        if not text or cue_end_ms <= cue_start_ms:
            continue

        for interval in render_intervals:
            source_start_ms = int(interval.get("source_start_ms") or 0)
            source_end_ms = int(interval.get("source_end_ms") or 0)
            requested_duration_ms = int(interval.get("requested_duration_ms") or 0)
            actual_duration_ms = int(interval.get("actual_duration_ms") or 0)
            preview_start_ms = int(interval.get("preview_start_ms") or 0)
            if source_end_ms <= source_start_ms or requested_duration_ms <= 0 or actual_duration_ms <= 0:
                continue

            overlap_start_ms = max(cue_start_ms, source_start_ms)
            overlap_end_ms = min(cue_end_ms, source_end_ms)
            if overlap_end_ms <= overlap_start_ms:
                continue

            scale = actual_duration_ms / requested_duration_ms
            mapped_start_ms = preview_start_ms + (overlap_start_ms - source_start_ms) * scale
            mapped_end_ms = preview_start_ms + (overlap_end_ms - source_start_ms) * scale
            if mapped_end_ms <= mapped_start_ms:
                mapped_end_ms = mapped_start_ms + 1

            lines.append(str(cue_index))
            lines.append(
                f"{_format_webvtt_time(mapped_start_ms)} --> {_format_webvtt_time(mapped_end_ms)}"
            )
            lines.append(text)
            lines.append("")
            cue_index += 1

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _get_cached_source_video(project: dict, temp_dir: Path) -> Path:
    source_ext = Path(project.get("source_filename") or "source.mp4").suffix or ".mp4"
    cached_path = source_cache_path(project["source_r2_key"], source_ext)
    cached_path.parent.mkdir(parents=True, exist_ok=True)
    if cached_path.is_file() and cached_path.stat().st_size > 0:
        return cached_path

    download_path = temp_dir / f"source_download{source_ext}"
    r2.download_file(project["source_r2_key"], str(download_path))
    download_path.replace(cached_path)
    return cached_path


def _render_final_preview(project_id: str, job_id: str | None) -> None:
    import shutil

    if not job_id:
        raise RuntimeError("final preview job_id is required")

    db = get_db()
    temp_dir = settings.avid_temp_dir / f"final_preview_{project_id}_{job_id[:8]}"

    try:
        job = db.table("jobs").select("*").eq("id", job_id).single().execute().data
        if not job:
            raise RuntimeError("final preview job not found")
        if job["status"] == "canceled":
            return
        if job["status"] == "completed":
            logger.info("Final preview job %s for project %s is already completed", job_id, project_id)
            return

        claimed = (
            db.table("jobs")
            .update({
                "status": "running",
                "progress": 0,
                "error_message": None,
                "started_at": "now()",
                "completed_at": None,
            })
            .eq("id", job_id)
            .eq("project_id", project_id)
            .in_("status", ["queued", "pending"])
            .execute()
        )
        if not claimed.data:
            logger.info(
                "Final preview job %s for project %s was already claimed or finished",
                job_id,
                project_id,
            )
            return
        logger.info("Rendering final preview job %s for project %s", job_id, project_id)

        project = db.table("projects").select("*").eq("id", project_id).single().execute().data
        if not project:
            raise RuntimeError("프로젝트를 찾을 수 없습니다")

        evaluation_payload = job.get("input_payload") or {}
        existing_result_keys = job.get("result_r2_keys") or {}
        hash_value = existing_result_keys.get("decision_hash") or final_preview_decision_hash(evaluation_payload)
        cache_token = existing_result_keys.get("cache_token") or new_cache_token()
        cache_video_path, cache_captions_path, cache_timeline_map_path = preview_cache_paths(
            project_id,
            hash_value,
        )

        if preview_cache_ready(project_id, hash_value):
            db.table("jobs").update({
                "status": "completed",
                "progress": 100,
                "result_r2_keys": {
                    "cache_key": preview_cache_key(project_id, hash_value),
                    "decision_hash": hash_value,
                    "cache_token": cache_token,
                    "duration_ms": existing_result_keys.get("duration_ms"),
                },
                "completed_at": "now()",
            }).eq("id", job_id).execute()
            logger.info("Final preview cache hit for project %s", project_id)
            return

        db.table("jobs").update({
            "status": "running",
            "progress": 5,
            "error_message": None,
            "started_at": "now()",
        }).eq("id", job_id).execute()

        temp_dir.mkdir(parents=True, exist_ok=True)
        output_dir = temp_dir / "output"
        output_dir.mkdir(exist_ok=True)

        completed_job = get_latest_artifact_job(db, project_id, select="id, result_r2_keys")
        if not completed_job:
            raise RuntimeError("완료된 기준 산출물이 없습니다")
        project_json_key = completed_job["result_r2_keys"].get("project_json")
        if not project_json_key:
            raise RuntimeError("프로젝트 JSON이 없습니다")

        project_json_bytes = r2.download_to_bytes(project_json_key)
        input_project_json = temp_dir / "input.project.avid.json"
        input_project_json.write_bytes(project_json_bytes)

        evaluation_path = temp_dir / "evaluation.json"
        evaluation_path.write_text(
            json.dumps(evaluation_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        db.table("jobs").update({"progress": 20}).eq("id", job_id).execute()

        applied_project_json = temp_dir / "01_eval_applied.project.avid.json"
        avid.apply_evaluation(
            project_json_path=str(input_project_json),
            evaluation_path=str(evaluation_path),
            output_project_json=str(applied_project_json),
        )
        db.table("jobs").update({"progress": 35}).eq("id", job_id).execute()

        intervals = _final_preview_intervals_from_project_json(applied_project_json)
        if not intervals:
            raise RuntimeError("미리보기로 렌더링할 keep 구간이 없습니다")
        db.table("jobs").update({"progress": 50}).eq("id", job_id).execute()

        source_path = _get_cached_source_video(project, temp_dir)

        no_subs_path = output_dir / "final_preview_no_subs.mp4"
        render_manifest = _render_intervals(source_path, intervals, no_subs_path)
        duration_ms = int((render_manifest.get("intervals") or [])[-1]["preview_end_ms"])
        db.table("jobs").update({"progress": 80}).eq("id", job_id).execute()

        captions_tmp_path = output_dir / "captions.vtt"
        _write_final_preview_webvtt_from_source_segments(
            applied_project_json,
            render_manifest,
            captions_tmp_path,
        )
        timeline_map_tmp_path = output_dir / "timeline_map.json"
        timeline_map_tmp_path.write_text(
            json.dumps(render_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        cache_video_path.parent.mkdir(parents=True, exist_ok=True)
        video_tmp_path = cache_video_path.with_suffix(".mp4.tmp")
        captions_cache_tmp_path = cache_captions_path.with_suffix(".vtt.tmp")
        timeline_map_cache_tmp_path = cache_timeline_map_path.with_suffix(".json.tmp")
        shutil.move(str(no_subs_path), str(video_tmp_path))
        shutil.move(str(captions_tmp_path), str(captions_cache_tmp_path))
        shutil.move(str(timeline_map_tmp_path), str(timeline_map_cache_tmp_path))
        video_tmp_path.replace(cache_video_path)
        captions_cache_tmp_path.replace(cache_captions_path)
        timeline_map_cache_tmp_path.replace(cache_timeline_map_path)

        db.table("jobs").update({
            "status": "completed",
            "progress": 100,
            "result_r2_keys": {
                "cache_key": preview_cache_key(project_id, hash_value),
                "decision_hash": hash_value,
                "cache_token": cache_token,
                "duration_ms": duration_ms,
            },
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        logger.info("Final preview completed for project %s", project_id)
    except Exception as exc:
        logger.exception("Final preview failed for project %s", project_id)
        db.table("jobs").update({
            "status": "failed",
            "error_message": str(exc)[:1000],
            "completed_at": "now()",
        }).eq("id", job_id).execute()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _register_source_identity(
    db,
    *,
    project_id: str,
    project: dict,
    source_path: str,
) -> str:
    path = Path(source_path)
    source_sha256 = source_cache.sha256_file(path)
    source_size_bytes = path.stat().st_size
    expected_sha256 = project.get("source_sha256")
    if expected_sha256 and expected_sha256 != source_sha256:
        raise RuntimeError("업로드된 원본 파일 해시가 프로젝트 생성 시 계산한 값과 다릅니다")

    db.table("projects").update({
        "source_sha256": source_sha256,
        "source_size_bytes": source_size_bytes,
    }).eq("id", project_id).execute()
    project["source_sha256"] = source_sha256
    project["source_size_bytes"] = source_size_bytes

    source_cache.upsert_source_asset(
        db,
        sha256=source_sha256,
        size_bytes=source_size_bytes,
        r2_key=project["source_r2_key"],
        filename=project.get("source_filename"),
        duration_seconds=project.get("source_duration_seconds"),
    )
    return source_sha256


def _transcribe_with_scribe_v2_cache(
    db,
    *,
    job_id: str,
    project: dict,
    source_path: str,
    output_dir: Path,
    source_sha256: str,
    language: str,
    transcription_context: str | None,
    diarize: bool,
    tag_audio_events: bool,
    num_speakers: int | None,
    use_llm_segmentation: bool,
    use_llm_refinement: bool,
    bypass_llm_segmentation_cache: bool,
    segmentation_boundary_rule: str,
    llm_log_path: Path | None = None,
    overlap_intervals_path: Path | None = None,
    retry_failed_size_cache: bool = False,
) -> chalna.TranscriptionSrtResult:
    source = Path(source_path)
    source_size_bytes = int(project.get("source_size_bytes") or source.stat().st_size)
    cache_params = scribe_v2_cache.ScribeV2CacheParams(
        source_sha256=source_sha256,
        source_size_bytes=source_size_bytes,
        language=language or "",
        diarize=diarize,
        num_speakers=num_speakers,
        tag_audio_events=tag_audio_events,
    )
    cache_key = scribe_v2_cache.build_scribe_v2_cache_key(cache_params)
    raw_json_local = output_dir / "source.scribe.raw.json"
    raw_srt_local = output_dir / "source.scribe.raw.srt"

    entry = scribe_v2_cache.get_cache_entry(db, cache_key)
    cache_owner = False
    cache_owner_token: str | None = None
    if entry and entry.get("status") == "completed":
        _download_completed_scribe_cache(db, entry, raw_json_local, raw_srt_local)
        _write_scribe_cache_pipeline_status(
            db,
            job_id,
            use_llm_segmentation=use_llm_segmentation,
            use_llm_refinement=use_llm_refinement,
            detail="동일 파일 raw Scribe V2 캐시 사용",
            completed=True,
        )
    elif entry and entry.get("status") == "running":
        _write_scribe_cache_pipeline_status(
            db,
            job_id,
            use_llm_segmentation=use_llm_segmentation,
            use_llm_refinement=use_llm_refinement,
            detail="동일 파일 raw Scribe V2 캐시 생성 대기 중",
            completed=False,
        )
        recovered = _recover_existing_raw_scribe_result(
            db,
            cache_key=cache_key,
            entry=entry,
            job_id=job_id,
            output_dir=output_dir,
            use_llm_segmentation=use_llm_segmentation,
            use_llm_refinement=use_llm_refinement,
            owner_token=entry.get("owner_token"),
            expected_status="running",
        )
        if recovered is not None:
            try:
                published = _complete_raw_scribe_cache(
                    db,
                    cache_key=cache_key,
                    result=recovered,
                    fallback_external_task_id=entry.get("external_task_id"),
                    owner_token=entry.get("owner_token"),
                    expected_status="running",
                )
            except Exception as exc:
                owner_token = entry.get("owner_token")
                if isinstance(owner_token, str) and owner_token:
                    scribe_v2_cache.mark_cache_failed(
                        db,
                        cache_key=cache_key,
                        owner_token=owner_token,
                        error_message=str(exc),
                        failure_kind="artifact_publish",
                        retryable=True,
                        resubmit_safe=False,
                    )
                raise
            if published:
                raw_json_local = Path(recovered.raw_json_path)
                raw_srt_local = Path(recovered.raw_srt_path)
            else:
                _download_authoritative_cache_after_owner_loss(
                    db,
                    cache_key=cache_key,
                    raw_json_local=raw_json_local,
                    raw_srt_local=raw_srt_local,
                )
        else:
            entry = scribe_v2_cache.get_cache_entry(db, cache_key) or entry
            if (
                entry.get("status") == "failed"
                and entry.get("retryable") is True
                and entry.get("resubmit_safe") is True
            ):
                candidate_token = scribe_v2_cache.new_owner_token()
                claimed_entry = scribe_v2_cache.claim_failed_entry_for_retry(
                    db,
                    cache_key=cache_key,
                    owner_token=candidate_token,
                    expected_attempt_count=int(entry.get("attempt_count") or 0),
                )
                cache_owner = claimed_entry is not None
                if claimed_entry is not None:
                    entry = claimed_entry
                    cache_owner_token = candidate_token
            if not cache_owner:
                entry = scribe_v2_cache.wait_for_running_entry(db, cache_key=cache_key)
                if entry.get("status") != "completed":
                    raise RuntimeError(entry.get("error_message") or "Scribe V2 cache generation failed")
                _download_completed_scribe_cache(db, entry, raw_json_local, raw_srt_local)
    elif entry and entry.get("status") == "failed":
        recovered = _recover_existing_raw_scribe_result(
            db,
            cache_key=cache_key,
            entry=entry,
            job_id=job_id,
            output_dir=output_dir,
            use_llm_segmentation=use_llm_segmentation,
            use_llm_refinement=use_llm_refinement,
            owner_token=entry.get("owner_token"),
            expected_status="failed",
        )
        if recovered is not None:
            published = _complete_raw_scribe_cache(
                db,
                cache_key=cache_key,
                result=recovered,
                fallback_external_task_id=entry.get("external_task_id"),
                owner_token=entry.get("owner_token"),
                expected_status="failed",
                expected_attempt_count=int(entry.get("attempt_count") or 0),
            )
            if published:
                raw_json_local = Path(recovered.raw_json_path)
                raw_srt_local = Path(recovered.raw_srt_path)
            else:
                _download_authoritative_cache_after_owner_loss(
                    db,
                    cache_key=cache_key,
                    raw_json_local=raw_json_local,
                    raw_srt_local=raw_srt_local,
                )
        else:
            entry = scribe_v2_cache.get_cache_entry(db, cache_key) or entry
            if entry.get("status") == "completed":
                _download_completed_scribe_cache(db, entry, raw_json_local, raw_srt_local)
            elif entry.get("status") == "running":
                entry = scribe_v2_cache.wait_for_running_entry(db, cache_key=cache_key)
                if entry.get("status") != "completed":
                    raise RuntimeError(entry.get("error_message") or "Scribe V2 cache generation failed")
                _download_completed_scribe_cache(db, entry, raw_json_local, raw_srt_local)
            else:
                legacy_rejected = retry_failed_size_cache and _is_chalna_file_size_cache_error(entry)
                if legacy_rejected:
                    # These legacy rows predate recovery metadata. The provider rejected
                    # the oversized request before accepting work, so resubmission of the
                    # newly generated audio proxy is known to be safe.
                    entry = {
                        **entry,
                        "retryable": True,
                        "resubmit_safe": True,
                        "failure_kind": "input_rejected_file_size",
                    }

                if entry.get("retryable") is True and entry.get("resubmit_safe") is True:
                    candidate_token = scribe_v2_cache.new_owner_token()
                    claimed_entry = scribe_v2_cache.claim_failed_entry_for_retry(
                        db,
                        cache_key=cache_key,
                        owner_token=candidate_token,
                        expected_attempt_count=int(entry.get("attempt_count") or 0),
                        require_resubmit_safe=not legacy_rejected,
                    )
                    cache_owner = claimed_entry is not None
                    if claimed_entry is not None:
                        entry = claimed_entry
                        cache_owner_token = candidate_token
                else:
                    raise RuntimeError(entry.get("error_message") or "Scribe V2 cache entry is failed")

                if not cache_owner:
                    entry = scribe_v2_cache.wait_for_running_entry(db, cache_key=cache_key)
                    if entry.get("status") != "completed":
                        raise RuntimeError(entry.get("error_message") or "Scribe V2 cache generation failed")
                    _download_completed_scribe_cache(db, entry, raw_json_local, raw_srt_local)
    else:
        candidate_token = scribe_v2_cache.new_owner_token()
        created_entry = scribe_v2_cache.create_running_entry(
            db,
            cache_key=cache_key,
            params=cache_params,
            owner_token=candidate_token,
        )
        cache_owner = created_entry is not None
        if created_entry is not None:
            entry = created_entry
            cache_owner_token = candidate_token
        if not cache_owner:
            entry = scribe_v2_cache.wait_for_running_entry(db, cache_key=cache_key)
            if entry.get("status") != "completed":
                raise RuntimeError(entry.get("error_message") or "Scribe V2 cache generation failed")
            _download_completed_scribe_cache(db, entry, raw_json_local, raw_srt_local)

    if cache_owner:
        if not cache_owner_token:
            raise RuntimeError("Scribe V2 cache owner token is missing")

        def _on_raw_scribe_status(payload: dict[str, object]) -> None:
            _update_chalna_pipeline_status(
                db,
                job_id,
                {
                    **payload,
                    "use_llm_segmentation": False,
                    "use_llm_refinement": False,
                },
            )
            scribe_v2_cache.record_provider_status(
                db,
                cache_key=cache_key,
                owner_token=cache_owner_token,
                payload=payload,
            )

        try:
            raw_result = chalna.transcribe_raw_scribe_to_files(
                source_path,
                language=language,
                output_dir=str(output_dir),
                diarize=diarize,
                tag_audio_events=tag_audio_events,
                num_speakers=num_speakers,
                on_status=_on_raw_scribe_status,
            )
        except Exception as exc:
            details = exc.details if isinstance(exc, chalna.ChalnaClientError) else {}
            if details:
                scribe_v2_cache.record_provider_status(
                    db,
                    cache_key=cache_key,
                    owner_token=cache_owner_token,
                    payload=details,
                )
            failure_published = scribe_v2_cache.mark_cache_failed(
                db,
                cache_key=cache_key,
                owner_token=cache_owner_token,
                error_message=str(exc),
                failure_kind=details.get("failure_kind"),
                retryable=details.get("retryable"),
                resubmit_safe=details.get("resubmit_safe"),
            )
            if failure_published:
                raise
            _download_authoritative_cache_after_owner_loss(
                db,
                cache_key=cache_key,
                raw_json_local=raw_json_local,
                raw_srt_local=raw_srt_local,
            )
        else:
            authoritative_downloaded = False
            try:
                published = _complete_raw_scribe_cache(
                    db,
                    cache_key=cache_key,
                    result=raw_result,
                    owner_token=cache_owner_token,
                    expected_status="running",
                )
            except Exception as exc:
                failure_published = scribe_v2_cache.mark_cache_failed(
                    db,
                    cache_key=cache_key,
                    owner_token=cache_owner_token,
                    error_message=str(exc),
                    failure_kind="artifact_publish",
                    retryable=True,
                    resubmit_safe=False,
                )
                if failure_published:
                    raise
                _download_authoritative_cache_after_owner_loss(
                    db,
                    cache_key=cache_key,
                    raw_json_local=raw_json_local,
                    raw_srt_local=raw_srt_local,
                )
                authoritative_downloaded = True
                published = False
            if published:
                raw_json_local = Path(raw_result.raw_json_path)
                raw_srt_local = Path(raw_result.raw_srt_path)
            elif not authoritative_downloaded:
                _download_authoritative_cache_after_owner_loss(
                    db,
                    cache_key=cache_key,
                    raw_json_local=raw_json_local,
                    raw_srt_local=raw_srt_local,
                )

    if (
        not use_llm_segmentation
        and not use_llm_refinement
        and segmentation_boundary_rule == DEFAULT_SEGMENTATION_BOUNDARY_RULE
        and overlap_intervals_path is None
    ):
        output_path = output_dir / "source.srt"
        output_path.write_text(raw_srt_local.read_text(encoding="utf-8"), encoding="utf-8")
        _write_scribe_cache_pipeline_status(
            db,
            job_id,
            use_llm_segmentation=False,
            use_llm_refinement=False,
            detail="raw Scribe V2 결과 사용",
            completed=True,
        )
        return chalna.TranscriptionSrtResult(
            srt_path=str(output_path),
            external_task_id="",
            metadata={"segmentation_source": "heuristic"},
            segmentation_log=[],
            processing_metadata={
                "segmentation_source": "heuristic",
                "segmentation_mode": "heuristic",
                "segmentation_label": "Heuristic",
                "fallback": False,
                "cache_hit": False,
                "cache_bypassed": False,
                "segmentation_boundary_rule": segmentation_boundary_rule,
                "segmentation_boundary_effective_rule": segmentation_boundary_rule,
            },
        )

    return chalna.transcribe_from_scribe_response_to_srt(
        source_path,
        str(raw_json_local),
        language=language,
        output_dir=str(output_dir),
        context=transcription_context,
        diarize=diarize,
        tag_audio_events=tag_audio_events,
        num_speakers=num_speakers,
        use_llm_segmentation=use_llm_segmentation,
        use_llm_refinement=use_llm_refinement,
        bypass_llm_segmentation_cache=bypass_llm_segmentation_cache,
        segmentation_boundary_rule=segmentation_boundary_rule,
        overlap_intervals_path=str(overlap_intervals_path) if overlap_intervals_path else None,
        llm_log_path=str(llm_log_path) if llm_log_path else None,
        on_status=lambda payload: _update_chalna_pipeline_status(
            db,
            job_id,
            {
                **payload,
                "use_llm_segmentation": use_llm_segmentation,
                "use_llm_refinement": use_llm_refinement,
            },
        ),
    )


def _recover_existing_raw_scribe_result(
    db,
    *,
    cache_key: str,
    entry: dict,
    job_id: str,
    output_dir: Path,
    use_llm_segmentation: bool,
    use_llm_refinement: bool,
    owner_token: str | None,
    expected_status: str,
) -> chalna.RawScribeResult | None:
    """Recover accepted provider work before considering another provider POST."""

    def _persist_status(payload: dict[str, object]) -> None:
        _update_chalna_pipeline_status(
            db,
            job_id,
            {
                **payload,
                "use_llm_segmentation": use_llm_segmentation,
                "use_llm_refinement": use_llm_refinement,
            },
        )
        if owner_token:
            scribe_v2_cache.record_provider_status(
                db,
                cache_key=cache_key,
                owner_token=owner_token,
                payload=payload,
                expected_status=expected_status,
            )

    transcription_id = entry.get("provider_transcription_id")
    if isinstance(transcription_id, str) and transcription_id:
        recovered = chalna.recover_provider_transcript_to_files(
            transcription_id,
            output_dir=str(output_dir),
            include_audio_events=bool(entry.get("tag_audio_events", True)),
        )
        if recovered is not None:
            logger.info(
                "Recovered Scribe cache %s from provider transcript %s without resubmission",
                cache_key,
                transcription_id,
            )
            return recovered

    external_task_id = entry.get("external_task_id")
    if not isinstance(external_task_id, str) or not external_task_id:
        return None

    try:
        recovered = chalna.resume_raw_scribe_job_to_files(
            external_task_id,
            output_dir=str(output_dir),
            on_status=_persist_status,
        )
    except chalna.ChalnaClientError as exc:
        details = exc.details
        if details and owner_token:
            scribe_v2_cache.record_provider_status(
                db,
                cache_key=cache_key,
                owner_token=owner_token,
                payload=details,
                expected_status=expected_status,
            )
        recovered_transcription_id = details.get("provider_transcription_id")
        if isinstance(recovered_transcription_id, str) and recovered_transcription_id:
            recovered = chalna.recover_provider_transcript_to_files(
                recovered_transcription_id,
                output_dir=str(output_dir),
                include_audio_events=bool(entry.get("tag_audio_events", True)),
            )
            if recovered is not None:
                return recovered
        if expected_status == "running" and owner_token:
            failure_published = scribe_v2_cache.mark_cache_failed(
                db,
                cache_key=cache_key,
                owner_token=owner_token,
                error_message=str(exc),
                failure_kind=details.get("failure_kind"),
                retryable=details.get("retryable"),
                resubmit_safe=details.get("resubmit_safe"),
            )
            if not failure_published:
                return None
        if details.get("resubmit_safe") is True:
            return None
        raise

    if recovered is not None:
        logger.info(
            "Recovered Scribe cache %s from accepted Chalna task %s without resubmission",
            cache_key,
            external_task_id,
        )
        return recovered

    # A missing Chalna task is not permission to duplicate accepted provider work.
    if entry.get("resubmit_safe") is not True:
        raise RuntimeError(
            "기존 Chalna 전사 작업을 찾을 수 없으며 provider 재요청의 안전성이 확인되지 않았습니다"
        )
    return None


def _complete_raw_scribe_cache(
    db,
    *,
    cache_key: str,
    result: chalna.RawScribeResult,
    fallback_external_task_id: str | None = None,
    owner_token: str | None,
    expected_status: str,
    expected_attempt_count: int | None = None,
) -> bool:
    raw_json_path = Path(result.raw_json_path)
    raw_srt_path = Path(result.raw_srt_path)
    if not raw_json_path.is_file() or raw_json_path.stat().st_size == 0:
        raise RuntimeError("Recovered Scribe raw JSON is missing or empty")
    if not raw_srt_path.is_file() or raw_srt_path.stat().st_size == 0:
        raise RuntimeError("Recovered Scribe raw SRT is missing or empty")

    if not owner_token:
        # Legacy failed rows are recovered by the explicit verified recovery
        # command. A live owner must always have a generation token.
        return False

    raw_json_key = scribe_v2_cache.attempt_raw_json_r2_key(cache_key, owner_token)
    raw_srt_key = scribe_v2_cache.attempt_raw_srt_r2_key(cache_key, owner_token)
    _upload_and_verify_scribe_cache_artifact(
        raw_json_path,
        raw_json_key,
        content_type="application/json",
    )
    _upload_and_verify_scribe_cache_artifact(
        raw_srt_path,
        raw_srt_key,
        content_type="text/plain",
    )
    completion_kwargs = {
        "cache_key": cache_key,
        "raw_json_key": raw_json_key,
        "raw_srt_key": raw_srt_key,
        "external_task_id": result.external_task_id or fallback_external_task_id,
        "provider_request_id": result.provider_request_id,
        "provider_transcription_id": result.provider_transcription_id,
        "provider_trace_id": result.provider_trace_id,
    }
    if expected_status == "failed":
        recovery_kwargs = {
            "expected_owner_token": owner_token,
            **completion_kwargs,
        }
        if expected_attempt_count is not None:
            recovery_kwargs["expected_attempt_count"] = expected_attempt_count
        return scribe_v2_cache.recover_failed_cache_as_completed(
            db,
            **recovery_kwargs,
        ) is not None
    return scribe_v2_cache.mark_cache_completed(
        db,
        owner_token=owner_token,
        **completion_kwargs,
    )


def _upload_and_verify_scribe_cache_artifact(
    local_path: Path,
    r2_key: str,
    *,
    content_type: str,
) -> None:
    local_bytes = local_path.read_bytes()
    local_sha256 = hashlib.sha256(local_bytes).hexdigest()
    r2.upload_file(str(local_path), r2_key, content_type)
    remote_bytes = r2.download_to_bytes(r2_key)
    remote_sha256 = hashlib.sha256(remote_bytes).hexdigest()
    if remote_sha256 != local_sha256:
        raise RuntimeError(
            f"Scribe cache artifact verification failed for {r2_key}: "
            f"local={local_sha256} remote={remote_sha256}"
        )


def _download_authoritative_cache_after_owner_loss(
    db,
    *,
    cache_key: str,
    raw_json_local: Path,
    raw_srt_local: Path,
) -> dict:
    entry = scribe_v2_cache.get_cache_entry(db, cache_key)
    if not entry:
        raise RuntimeError("Scribe V2 cache row disappeared after owner generation changed")
    if entry.get("status") == "running":
        entry = scribe_v2_cache.wait_for_running_entry(db, cache_key=cache_key)
    if entry.get("status") != "completed":
        raise RuntimeError(entry.get("error_message") or "Newer Scribe V2 cache generation failed")
    _download_completed_scribe_cache(db, entry, raw_json_local, raw_srt_local)
    return entry


def _download_completed_scribe_cache(
    db,
    entry: dict,
    raw_json_local: Path,
    raw_srt_local: Path,
) -> None:
    raw_json_key = entry.get("raw_json_r2_key")
    raw_srt_key = entry.get("raw_srt_r2_key")
    if not raw_json_key or not raw_srt_key:
        raise RuntimeError("Scribe V2 cache entry is completed but missing raw artifacts")
    r2.download_file(raw_json_key, str(raw_json_local))
    r2.download_file(raw_srt_key, str(raw_srt_local))
    scribe_v2_cache.record_cache_hit(db, entry)


def _is_chalna_file_size_cache_error(entry: dict) -> bool:
    error_message = str(entry.get("error_message") or "")
    return (
        "FileTooLargeError" in error_message
        or '"error_code":"E1004"' in error_message
        or "exceeds maximum allowed" in error_message
    )


def _download_reused_transcription_srt(
    db,
    *,
    job_id: str,
    project_settings: dict,
    output_dir: Path,
) -> chalna.TranscriptionSrtResult | None:
    srt_key = project_settings.get("reused_transcription_srt_r2_key")
    if not isinstance(srt_key, str) or not srt_key:
        return None

    srt_path = output_dir / "source.srt"
    try:
        r2.download_file(srt_key, str(srt_path))
    except Exception:
        logger.exception("Failed to reuse transcription SRT %s for job %s", srt_key, job_id)
        _write_reused_transcription_pipeline_status(
            db,
            job_id,
            status="running",
            progress=1,
            detail="기존 자막 재사용 실패, Scribe cache 경로 사용",
        )
        return None

    _write_reused_transcription_pipeline_status(
        db,
        job_id,
        status="completed",
        progress=100,
        detail="부모 프로젝트의 LLM-refined SRT를 사용",
    )
    logger.info(
        "Reused transcription SRT for job %s from project %s job %s",
        job_id,
        project_settings.get("reused_transcription_from_project_id"),
        project_settings.get("reused_transcription_from_job_id"),
    )
    return chalna.TranscriptionSrtResult(
        srt_path=str(srt_path),
        external_task_id="",
        metadata={"segmentation_source": "reused"},
        segmentation_log=[],
        processing_metadata={
            "segmentation_source": "reused",
            "segmentation_mode": "reused_srt",
            "segmentation_label": "Reused SRT",
            "fallback": False,
            "cache_hit": False,
            "cache_bypassed": False,
            "reused_from_project_id": project_settings.get("reused_transcription_from_project_id"),
            "reused_from_job_id": project_settings.get("reused_transcription_from_job_id"),
        },
    )


def _write_reused_transcription_pipeline_status(
    db,
    job_id: str,
    *,
    status: str,
    progress: int,
    detail: str,
) -> None:
    stages = _initial_pipeline_stages(use_llm_refinement=False)
    stages[0].update({
        "status": "completed",
        "progress": 100,
        "detail": "미디어 검증 완료",
    })
    stages[1].update({
        "id": "reused_transcription_srt",
        "label": "기존 자막 재사용",
        "status": status,
        "progress": progress,
        "detail": detail,
    })
    db.table("jobs").update({
        "pipeline_stages": stages,
        "progress": 30 if status == "completed" else 10,
    }).eq("id", job_id).execute()


def _write_scribe_cache_pipeline_status(
    db,
    job_id: str,
    *,
    use_llm_segmentation: bool,
    use_llm_refinement: bool,
    detail: str,
    completed: bool,
) -> None:
    stages = _initial_pipeline_stages(
        use_llm_segmentation=use_llm_segmentation,
        use_llm_refinement=use_llm_refinement,
    )
    stages[0].update({
        "status": "completed",
        "progress": 100,
        "detail": "미디어 검증 완료",
    })
    stages[1].update({
        "label": "Scribe V2 cache",
        "status": "completed" if completed else "running",
        "progress": 100 if completed else 1,
        "detail": detail,
    })
    db.table("jobs").update({
        "pipeline_stages": stages,
        "progress": 30 if completed else 10,
    }).eq("id", job_id).execute()


def _update_progress(db, job_id: str, progress: int) -> None:
    db.table("jobs").update({"progress": progress}).eq("id", job_id).execute()


def _bool_project_setting(settings_value: dict, key: str, *, default: bool) -> bool:
    value = settings_value.get(key)
    return value if isinstance(value, bool) else default


def _optional_int_project_setting(settings_value: dict, key: str) -> int | None:
    value = settings_value.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cut_decision_pipeline_stages(
    current_stage: str | None = None,
    stage_progress: int = 0,
    *,
    failed: bool = False,
    completed: bool = False,
) -> list[dict[str, object]]:
    definitions = [
        (
            "reuse_segments",
            "기존 segment/refine 재사용",
            "저장된 refined segment 확인 대기 중",
            "저장된 refined segment 확인 중",
            "저장된 refined segment 재사용",
        ),
        (
            "edit_decision",
            "Cut decision 재실행",
            "cut decision 생성 대기 중",
            "cut decision 생성 중",
            "cut decision 생성 완료",
        ),
        (
            "upload_results",
            "결과 저장",
            "새 편집 결과 저장 대기 중",
            "새 편집 결과 저장 중",
            "새 편집 결과 저장 완료",
        ),
    ]
    current_index = next(
        (index for index, item in enumerate(definitions) if item[0] == current_stage),
        -1,
    )
    stages: list[dict[str, object]] = []
    for index, (stage_id, label, pending_detail, running_detail, completed_detail) in enumerate(definitions):
        stage: dict[str, object] = {
            "id": stage_id,
            "label": label,
            "status": "pending",
            "progress": 0,
            "detail": pending_detail,
        }
        if completed or (current_index >= 0 and index < current_index):
            stage.update({"status": "completed", "progress": 100, "detail": completed_detail})
        elif current_stage == stage_id:
            if failed:
                stage.update({"status": "failed", "progress": max(1, stage_progress), "detail": "처리 실패"})
            else:
                stage.update({
                    "status": "running",
                    "progress": max(1, min(100, int(stage_progress))),
                    "detail": running_detail,
                })
        stages.append(stage)
    return stages


def _update_cut_decision_progress(
    db,
    job_id: str,
    progress: int,
    current_stage: str,
    stage_progress: int,
) -> None:
    db.table("jobs").update({
        "progress": progress,
        "pipeline_stages": _cut_decision_pipeline_stages(current_stage, stage_progress),
    }).eq("id", job_id).execute()


def _initial_pipeline_stages(
    *,
    use_llm_segmentation: bool = True,
    use_llm_refinement: bool = True,
) -> list[dict[str, object]]:
    transcribe_label = (
        "Scribe V2 + LLM segmentation"
        if use_llm_segmentation
        else "Scribe V2 transcription"
    )
    transcribe_detail = (
        "Scribe V2 전사 및 자막 구간 나누기 대기 중"
        if use_llm_segmentation
        else "Scribe V2 전사 대기 중"
    )
    stages: list[dict[str, object]] = [
        {
            "id": "validate_audio",
            "label": "입력 검증",
            "status": "pending",
            "progress": 0,
            "detail": "미디어 검증 대기 중",
        },
        {
            "id": "scribe_v2_transcribe",
            "label": transcribe_label,
            "status": "pending",
            "progress": 0,
            "detail": transcribe_detail,
        },
    ]
    if use_llm_refinement:
        stages.append({
            "id": "llm_refine",
            "label": "LLM refine",
            "status": "pending",
            "progress": 0,
            "detail": "문장 정제 대기 중",
        })
    return stages


def _update_chalna_pipeline_status(db, job_id: str | None, payload: dict[str, object]) -> None:
    if not job_id:
        return

    try:
        update_payload: dict[str, object] = {
            "pipeline_stages": _build_chalna_pipeline_stages(payload),
        }

        chalna_job_id = payload.get("job_id")
        if isinstance(chalna_job_id, str) and chalna_job_id:
            update_payload["external_task_ids"] = {"chalna": chalna_job_id}

        progress = payload.get("progress")
        if isinstance(progress, (int, float)):
            update_payload["progress"] = min(30, max(10, int(10 + float(progress) * 20)))

        db.table("jobs").update(update_payload).eq("id", job_id).execute()
    except Exception:
        logger.exception("Failed to persist Chalna pipeline status for job %s", job_id)


def _build_chalna_pipeline_stages(payload: dict[str, object]) -> list[dict[str, object]]:
    use_llm_segmentation = payload.get("use_llm_segmentation")
    use_llm_refinement = payload.get("use_llm_refinement")
    stages = _initial_pipeline_stages(
        use_llm_segmentation=(
            use_llm_segmentation if isinstance(use_llm_segmentation, bool) else True
        ),
        use_llm_refinement=use_llm_refinement if isinstance(use_llm_refinement, bool) else True,
    )
    status = str(payload.get("status") or "")
    current_stage = str(payload.get("current_stage") or "")
    history_value = payload.get("progress_history") or []
    history = history_value if isinstance(history_value, list) else []
    latest = _latest_stage_entries(history)

    current_rank = {
        "validating": 0,
        "transcribing": 1,
        "refining": 2,
    }.get(current_stage, -1)

    _apply_stage(
        stages[0],
        latest.get("validating"),
        running=current_stage == "validating",
        completed=current_rank > 0 or "transcribing" in latest or status == "completed",
        failed=status == "failed" and current_stage == "validating",
        pending_detail="미디어 검증 대기 중",
        running_detail="미디어 검증 중",
        completed_detail="미디어 검증 완료",
    )
    transcribing_pending_detail = (
        "Scribe V2 전사 및 자막 구간 나누기 대기 중"
        if (use_llm_segmentation if isinstance(use_llm_segmentation, bool) else True)
        else "Scribe V2 전사 대기 중"
    )
    transcribing_running_detail = (
        "Scribe V2 전사 및 자막 구간 나누기 진행 중"
        if (use_llm_segmentation if isinstance(use_llm_segmentation, bool) else True)
        else "Scribe V2 전사 진행 중"
    )
    transcribing_completed_detail = (
        "Scribe V2 전사 및 자막 구간 나누기 완료"
        if (use_llm_segmentation if isinstance(use_llm_segmentation, bool) else True)
        else "Scribe V2 전사 완료"
    )
    transcribing_entry = latest.get("transcribing")
    segmentation_enabled = use_llm_segmentation if isinstance(use_llm_segmentation, bool) else True
    if (
        isinstance(transcribing_entry, dict)
        and transcribing_entry.get("cache_hit") is True
        and transcribing_entry.get("source") == "provided_scribe_response"
    ):
        if segmentation_enabled:
            stages[1]["label"] = "Scribe V2 cache + LLM segmentation"
            transcribing_running_detail = "캐시된 raw Scribe V2 결과로 자막 구간 나누기 진행 중"
            transcribing_completed_detail = "동일 파일 raw Scribe V2 캐시 사용 및 자막 구간 나누기 완료"
        else:
            stages[1]["label"] = "Scribe V2 cache"
            transcribing_running_detail = "캐시된 raw Scribe V2 결과 사용 중"
            transcribing_completed_detail = "동일 파일 raw Scribe V2 캐시 사용"
    _apply_stage(
        stages[1],
        transcribing_entry,
        running=current_stage == "transcribing",
        completed=current_rank > 1 or "refining" in latest or status == "completed",
        failed=status == "failed" and current_stage == "transcribing",
        pending_detail=transcribing_pending_detail,
        running_detail=_chalna_chunk_detail(payload, transcribing_running_detail),
        completed_detail=transcribing_completed_detail,
    )
    if len(stages) > 2:
        _apply_stage(
            stages[2],
            latest.get("refining"),
            running=current_stage == "refining",
            completed=status == "completed",
            failed=status == "failed" and current_stage == "refining",
            pending_detail="문장 정제 대기 중",
            running_detail="문장 정제 진행 중",
            completed_detail="문장 정제 완료",
        )

    return stages


def _latest_stage_entries(history: list[object]) -> dict[str, dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for entry in history:
        if not isinstance(entry, dict):
            continue
        stage = entry.get("stage")
        if isinstance(stage, str):
            latest[stage] = entry
    return latest


def _apply_stage(
    stage: dict[str, object],
    entry: dict[str, object] | None,
    *,
    running: bool,
    completed: bool,
    failed: bool,
    pending_detail: str,
    running_detail: str,
    completed_detail: str,
) -> None:
    progress = round(_stage_progress(entry) * 100) if entry else 0
    timestamp = _entry_timestamp(entry)

    stage["progress"] = progress
    if timestamp:
        stage["started_at"] = timestamp

    if failed:
        stage.update({"status": "failed", "detail": "처리 실패"})
        return
    if completed:
        stage.update({"status": "completed", "progress": 100, "detail": completed_detail})
        if timestamp:
            stage["completed_at"] = timestamp
        return
    if running or entry:
        stage.update({"status": "running", "progress": max(1, progress), "detail": running_detail})
        return

    stage["detail"] = pending_detail


def _stage_progress(entry: dict[str, object] | None) -> float:
    if not entry:
        return 0.0
    value = entry.get("progress")
    if not isinstance(value, (int, float)):
        return 0.0
    numeric = float(value)
    if numeric > 1.0:
        numeric = numeric / 100.0
    return max(0.0, min(1.0, numeric))


def _entry_timestamp(entry: dict[str, object] | None) -> str | None:
    if not entry:
        return None
    timestamp = entry.get("timestamp")
    return timestamp if isinstance(timestamp, str) and timestamp else None


def _chalna_chunk_detail(payload: dict[str, object], fallback: str) -> str:
    chunk = payload.get("chunks_completed")
    total = payload.get("total_chunks")
    if isinstance(chunk, int) and isinstance(total, int) and total > 0:
        return f"음성 인식 chunk {chunk}/{total} 처리 중"
    return fallback


def _save_report(db, project_id: str, total_duration: int, report_markdown: str) -> None:
    import re

    cut_duration = 0
    cut_percentage = 0.0

    # Try to find "합계" row in markdown table: | **합계** | **878개** | **24:31.665** |
    total_match = re.search(r"합계.*?\|\s*\**(\d+):(\d+)\.(\d+)\**\s*\|", report_markdown)
    if total_match:
        minutes = int(total_match.group(1))
        seconds = int(total_match.group(2))
        cut_duration = minutes * 60 + seconds
        if total_duration > 0:
            cut_percentage = round(cut_duration / total_duration * 100, 1)
    else:
        # Fallback: look for percentage pattern near "절약" or "saved"
        for line in report_markdown.split("\n"):
            if "절약" in line or "saved" in line.lower():
                pct_match = re.search(r"(\d+\.?\d*)%", line)
                if pct_match:
                    cut_percentage = float(pct_match.group(1))
                    cut_duration = int(total_duration * cut_percentage / 100)
                    break

    db.table("edit_reports").insert({
        "project_id": project_id,
        "total_duration_seconds": total_duration,
        "cut_duration_seconds": cut_duration,
        "cut_percentage": cut_percentage,
        "edit_summary": {},
        "report_markdown": report_markdown,
    }).execute()


def _guess_content_type(key: str) -> str:
    types = {
        "fcpxml": "application/xml",
        "srt": "text/plain",
        "report": "text/markdown",
        "project_json": "application/json",
        "storyline": "application/json",
        "segments_json": "application/json",
        "overlap_protection": "application/json",
        "sync_diagnostics": "application/json",
        "llm_io_log": "application/x-ndjson",
        "preview": "video/mp4",
    }
    return types.get(key, "application/octet-stream")
