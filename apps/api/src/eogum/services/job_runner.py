"""Sequential job runner for processing video projects."""

import json
import logging
import hashlib
import subprocess
import threading
from collections import deque
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET

from eogum.config import settings
from eogum.services import avid, chalna, credit, email, r2
from eogum.services.artifacts import get_latest_artifact_job
from eogum.services.database import get_db

logger = logging.getLogger(__name__)

_queue: deque[dict[str, str | None]] = deque()
_running = False
_lock = threading.Lock()


def enqueue(project_id: str) -> None:
    """Add project to processing queue."""
    _queue.append({"kind": "initial", "project_id": project_id, "job_id": None})
    _maybe_start_worker()


def enqueue_reprocess(project_id: str, job_id: str) -> None:
    """Add reprocess task to queue."""
    _queue.append({"kind": "reprocess", "project_id": project_id, "job_id": job_id})
    _maybe_start_worker()


def enqueue_final_preview(project_id: str, job_id: str) -> None:
    """Add final-preview render task to queue."""
    _queue.append({"kind": "final_preview", "project_id": project_id, "job_id": job_id})
    _maybe_start_worker()


def _maybe_start_worker() -> None:
    global _running
    with _lock:
        if _running:
            return
        _running = True
    thread = threading.Thread(target=_worker_loop, daemon=True)
    thread.start()


def _worker_loop() -> None:
    global _running
    while _queue:
        item = _queue.popleft()
        project_id = item["project_id"]
        try:
            if item["kind"] == "reprocess":
                _reprocess_project(project_id, item["job_id"])
            elif item["kind"] == "final_preview":
                _render_final_preview(project_id, item["job_id"])
            else:
                _process_project(project_id)
        except Exception:
            logger.exception("Fatal error processing project %s", project_id)
    with _lock:
        _running = False


def _mark_initial_project_failure(db, project_id: str, *, job_id: str | None, error_message: str) -> None:
    """Best-effort cleanup for failures before normal job failure handling starts."""
    resolved_job_id = job_id
    if not resolved_job_id:
        latest_incomplete = (
            db.table("jobs")
            .select("id")
            .eq("project_id", project_id)
            .in_("status", ["running", "pending"])
            .order("created_at", desc=True)
            .limit(1)
            .maybe_single()
            .execute()
        )
        if latest_incomplete.data:
            resolved_job_id = latest_incomplete.data["id"]

    if resolved_job_id:
        db.table("jobs").update({
            "status": "failed",
            "error_message": error_message[:1000],
            "completed_at": "now()",
        }).eq("id", resolved_job_id).execute()

    db.table("projects").update({"status": "failed"}).eq("id", project_id).execute()


def _process_project(project_id: str) -> None:
    db = get_db()
    temp_dir = settings.avid_temp_dir / project_id
    project = None
    user_id = None
    user_email = None
    duration = 0
    job_id = None
    credits_held = False

    try:
        # Load project
        project = db.table("projects").select("*").eq("id", project_id).single().execute().data
        user_id = project["user_id"]
        duration = project["source_duration_seconds"]

        # Get user email
        db.table("profiles").select("id, display_name").eq("id", user_id).single().execute()
        auth_user = db.auth.admin.get_user_by_id(user_id)
        user_email = auth_user.user.email

        # Update project status
        db.table("projects").update({"status": "processing"}).eq("id", project_id).execute()

        # Create job record
        job = db.table("jobs").insert({
            "project_id": project_id,
            "user_id": user_id,
            "type": project["cut_type"],
            "status": "running",
            "pipeline_stages": _initial_pipeline_stages(),
            "external_task_ids": {},
        }).execute().data[0]
        job_id = job["id"]

        # Ensure temp dirs
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_dir = temp_dir / "output"
        output_dir.mkdir(exist_ok=True)

        # 1. Hold credits
        credit.hold_credits(user_id, duration, job_id)
        credits_held = True

        # 2. Download source from R2
        source_ext = Path(project["source_filename"]).suffix
        source_path = str(temp_dir / f"source{source_ext}")
        _update_progress(db, job_id, 5)

        r2.download_file(project["source_r2_key"], source_path)

        # 2.5. Download extra sources (multicam)
        extra_source_paths: list[str] = []
        for i, es in enumerate(project.get("extra_sources") or []):
            ext = Path(es["filename"]).suffix
            local_path = str(temp_dir / f"extra_{i}{ext}")
            r2.download_file(es["r2_key"], local_path)
            extra_source_paths.append(local_path)

        _update_progress(db, job_id, 10)

        # 3. Transcribe
        project_settings = project.get("settings") or {}
        transcription_context = project_settings.get("transcription_context")
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
        srt_path = chalna.transcribe_to_srt(
            source_path,
            language=project["language"],
            output_dir=str(temp_dir),
            context=transcription_context,
            diarize=_bool_project_setting(project_settings, "diarize", default=True),
            tag_audio_events=_bool_project_setting(
                project_settings,
                "tag_audio_events",
                default=True,
            ),
            num_speakers=_optional_int_project_setting(project_settings, "num_speakers"),
            use_llm_segmentation=use_llm_segmentation,
            use_llm_refinement=use_llm_refinement,
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
        _update_progress(db, job_id, 30)

        # 4. Transcript overview (Pass 1)
        storyline_path = avid.transcript_overview(srt_path, output_path=str(output_dir / "storyline.json"))
        _update_progress(db, job_id, 50)

        # 5. Cut (Pass 2)
        cut_fn = avid.subtitle_cut if project["cut_type"] == "subtitle_cut" else avid.podcast_cut
        result_paths = cut_fn(
            source_path=source_path,
            srt_path=srt_path,
            context_path=storyline_path,
            output_dir=str(output_dir),
            extra_sources=extra_source_paths or None,
            target_duration_minutes=_output_target_duration_minutes(project),
        )
        _update_progress(db, job_id, 75)

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
        db.table("jobs").update({
            "status": "completed",
            "progress": 100,
            "result_r2_keys": r2_keys,
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
        # Cleanup temp files
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


def _output_target_duration_minutes(project: dict) -> int | None:
    target = (project.get("settings") or {}).get("output_target_duration_minutes")
    if target is None or isinstance(target, bool):
        return None
    try:
        target_minutes = int(target)
    except (TypeError, ValueError):
        return None
    if target_minutes not in {20, 40, 60}:
        return None
    return target_minutes


def _resolve_extra_source_offsets(extra_sources: list[dict]) -> list[int] | None:
    if not extra_sources:
        return None

    offsets = [item.get("offset_ms") for item in extra_sources]
    if not any(offset is not None for offset in offsets):
        return None
    if not all(offset is not None for offset in offsets):
        raise ValueError("manual offset 을 사용할 때는 모든 extra source 에 offset_ms 를 지정해야 합니다")
    return [int(offset) for offset in offsets]


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
    row = db.table("jobs").select("status").eq("id", job_id).maybe_single().execute()
    return bool(row.data and row.data.get("status") in {"cancel_requested", "canceled"})


def _raise_if_canceled(db, job_id: str) -> None:
    if _is_job_canceled(db, job_id):
        raise JobCanceled("작업 취소가 요청되었습니다")


def _update_multicam_state(db, project_id: str, **updates) -> None:
    project = db.table("projects").select("multicam_state").eq("id", project_id).maybe_single().execute()
    state = dict(project.data.get("multicam_state") or {}) if project.data else {}
    state.update(updates)
    db.table("projects").update({"multicam_state": state}).eq("id", project_id).execute()


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
    project = db.table("projects").select("*").eq("id", project_id).single().execute().data
    user_id = project["user_id"]

    def cancel_check() -> bool:
        return _is_job_canceled(db, job_id)

    try:
        _raise_if_canceled(db, job_id)
    except JobCanceled:
        db.table("jobs").update({
            "status": "canceled",
            "progress": 0,
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        db.table("projects").update({"status": "completed"}).eq("id", project_id).execute()
        _update_multicam_state(db, project_id, status="canceled", job_id=job_id)
        return

    db.table("projects").update({"status": "processing"}).eq("id", project_id).execute()
    db.table("jobs").update({
        "status": "running",
        "progress": 5,
        "error_message": None,
    }).eq("id", job_id).execute()
    _update_multicam_state(db, project_id, status="running", job_id=job_id, error=None)

    temp_dir = settings.avid_temp_dir / f"multicam_{project_id}"
    try:
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

        eval_result = (
            db.table("evaluations")
            .select("segments")
            .eq("project_id", project_id)
            .eq("evaluator_id", user_id)
            .limit(1)
            .execute()
        )
        evaluation_payload = eval_result.data[0]["segments"] if eval_result.data else None
        if isinstance(evaluation_payload, dict):
            eval_segments = evaluation_payload.get("segments") or []
        else:
            eval_segments = evaluation_payload

        has_extra_sources = bool(project.get("extra_sources"))
        extra_offsets = _resolve_extra_source_offsets(project.get("extra_sources") or [])

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

        source_path = None
        extra_paths: list[str] = []
        if has_extra_sources:
            source_ext = Path(project["source_filename"]).suffix
            local_source_path = temp_dir / f"source{source_ext}"
            _raise_if_canceled(db, job_id)
            r2.download_file(project["source_r2_key"], str(local_source_path))
            _raise_if_canceled(db, job_id)
            source_path = str(local_source_path)

            for i, es in enumerate(project["extra_sources"]):
                ext = Path(es["filename"]).suffix
                local_path = temp_dir / f"extra_{i}{ext}"
                _raise_if_canceled(db, job_id)
                r2.download_file(es["r2_key"], str(local_path))
                _raise_if_canceled(db, job_id)
                extra_paths.append(str(local_path))

        steps = _plan_reprocess_steps(
            has_evaluation=bool(eval_segments),
            desired_extra_sources=has_extra_sources,
            current_project_has_extra_sources=_project_json_has_extra_sources(working_project_json),
        )

        db.table("jobs").update({"progress": 25}).eq("id", job_id).execute()

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
            payload = avid.rebuild_multicam(
                project_json_path=str(working_project_json),
                source_path=str(source_path),
                extra_sources=extra_paths,
                output_project_json=str(multicam_output),
                offsets=extra_offsets,
                is_canceled=cancel_check,
            )
            _raise_if_canceled(db, job_id)
            working_project_json = Path(payload["artifacts"]["project_json"])
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

        db.table("jobs").update({"progress": 70}).eq("id", job_id).execute()

        _raise_if_canceled(db, job_id)
        payload = avid.export_project(
            project_json_path=str(working_project_json),
            output_dir=str(output_dir),
            content_mode="cut" if eval_segments else "disabled",
            is_canceled=cancel_check,
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

        sync_diagnostics_path = artifacts.get("sync_diagnostics")
        if sync_diagnostics_path:
            sync_path = Path(sync_diagnostics_path)
            sync_r2_key = f"results/{project_id}/{sync_path.name}"
            _raise_if_canceled(db, job_id)
            r2.upload_file(str(sync_path), sync_r2_key, "application/json")
            new_r2_keys["sync_diagnostics"] = sync_r2_key
        elif "sync_diagnostics" in new_r2_keys:
            new_r2_keys.pop("sync_diagnostics", None)

        db.table("jobs").update({
            "status": "completed",
            "progress": 100,
            "result_r2_keys": new_r2_keys,
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        applied_hash = _extra_sources_hash(project.get("extra_sources") or [])
        db.table("projects").update({
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
        }).eq("id", project_id).execute()
        logger.info("Reprocess completed for project %s", project_id)
    except (JobCanceled, avid.AvidCommandCanceled):
        logger.info("Project reprocess canceled for project %s", project_id)
        db.table("jobs").update({
            "status": "canceled",
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        db.table("projects").update({"status": "completed"}).eq("id", project_id).execute()
        _update_multicam_state(db, project_id, status="canceled", job_id=job_id, error=None)
    except Exception as exc:
        logger.exception("Project reprocess failed for project %s", project_id)
        db.table("jobs").update({
            "status": "failed",
            "error_message": str(exc)[:1000],
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        db.table("projects").update({"status": "reprocess_failed"}).eq("id", project_id).execute()
        _update_multicam_state(db, project_id, status="failed", job_id=job_id, error=str(exc)[:1000])
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


def _primary_intervals_from_fcpxml(fcpxml_path: Path) -> list[tuple[float, float]]:
    root = ET.parse(fcpxml_path).getroot()
    spine = root.find("./library/event/project/sequence/spine")
    if spine is None:
        return []

    intervals: list[tuple[float, float]] = []
    for clip in list(spine):
        if clip.tag != "asset-clip" or clip.get("lane") is not None:
            continue
        if clip.get("enabled") == "0":
            continue
        start = _fcpxml_time_seconds(clip.get("start"))
        duration = _fcpxml_time_seconds(clip.get("duration"))
        if duration > 0:
            intervals.append((start, duration))
    return intervals


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


def _render_intervals(source_path: Path, intervals: list[tuple[float, float]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_script = output_path.with_suffix(".filter.txt")
    has_audio = _has_audio_stream(source_path)

    lines: list[str] = []
    concat_inputs: list[str] = []
    for index, (start, duration) in enumerate(intervals):
        lines.append(
            f"[0:v]trim=start={start:.6f}:duration={duration:.6f},"
            f"setpts=PTS-STARTPTS[v{index}]"
        )
        concat_inputs.append(f"[v{index}]")
        if has_audio:
            lines.append(
                f"[0:a]atrim=start={start:.6f}:duration={duration:.6f},"
                f"asetpts=PTS-STARTPTS[a{index}]"
            )
            concat_inputs.append(f"[a{index}]")

    concat_args = "".join(concat_inputs)
    if has_audio:
        lines.append(f"{concat_args}concat=n={len(intervals)}:v=1:a=1[vcat][acat]")
    else:
        lines.append(f"{concat_args}concat=n={len(intervals)}:v=1:a=0[vcat]")
    filter_script.write_text(";\n".join(lines), encoding="utf-8")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(source_path),
        "-filter_complex_script",
        str(filter_script),
        "-map",
        "[vcat]",
    ]
    if has_audio:
        cmd += ["-map", "[acat]"]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd += ["-movflags", "+faststart", str(output_path)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if result.returncode != 0:
        raise RuntimeError(f"final preview render failed: {result.stderr[-1000:]}")


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

        project = db.table("projects").select("*").eq("id", project_id).single().execute().data
        if not project:
            raise RuntimeError("프로젝트를 찾을 수 없습니다")

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
        evaluation_payload = job.get("input_payload") or {}
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

        export_payload = avid.export_project(
            project_json_path=str(applied_project_json),
            output_dir=str(output_dir),
            silence_mode="cut",
            content_mode="cut",
        )
        artifacts = export_payload.get("artifacts") or {}
        fcpxml_path = Path(artifacts["fcpxml"])
        srt_path = Path(artifacts["srt"]) if artifacts.get("srt") else None
        intervals = _primary_intervals_from_fcpxml(fcpxml_path)
        if not intervals:
            raise RuntimeError("미리보기로 렌더링할 keep 구간이 없습니다")
        duration_ms = int(sum(duration for _, duration in intervals) * 1000)
        db.table("jobs").update({"progress": 50}).eq("id", job_id).execute()

        source_ext = Path(project["source_filename"] or "source.mp4").suffix or ".mp4"
        source_path = temp_dir / f"source{source_ext}"
        r2.download_file(project["source_r2_key"], str(source_path))

        no_subs_path = output_dir / "final_preview_no_subs.mp4"
        final_path = output_dir / "final_preview.mp4"
        _render_intervals(source_path, intervals, no_subs_path)
        db.table("jobs").update({"progress": 80}).eq("id", job_id).execute()

        _burn_subtitles(no_subs_path, srt_path, final_path)
        final_r2_key = f"results/{project_id}/final_preview_{job_id}.mp4"
        r2.upload_file(str(final_path), final_r2_key, "video/mp4")

        db.table("jobs").update({
            "status": "completed",
            "progress": 100,
            "result_r2_keys": {
                "final_preview": final_r2_key,
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
    _apply_stage(
        stages[1],
        latest.get("transcribing"),
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
        "sync_diagnostics": "application/json",
        "preview": "video/mp4",
    }
    return types.get(key, "application/octet-stream")
