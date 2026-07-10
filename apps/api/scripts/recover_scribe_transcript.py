#!/usr/bin/env python3
"""Recover a completed ElevenLabs transcript without submitting new provider work.

Run from ``apps/api`` after the Scribe recovery migration and Chalna recovery
endpoint have been deployed::

    PYTHONPATH=src .venv/bin/python scripts/recover_scribe_transcript.py \
      --project-id <project-id> \
      --transcription-id <provider-transcription-id> \
      --external-task-id <chalna-job-id> \
      --apply --activate-project

The command deliberately publishes the cache row only after both deterministic
R2 objects have been downloaded and verified.  Project activation creates a
linked pending attempt before changing the project to ``queued``; the running
API sweeper (or its startup recovery) then owns enqueueing the work.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eogum.services import chalna, r2, scribe_v2_cache  # noqa: E402
from eogum.services.credit import get_balance  # noqa: E402
from eogum.services.database import get_db  # noqa: E402
from eogum.services.job_runner import create_initial_job  # noqa: E402


SRT_TIMESTAMP_RE = re.compile(
    r"(?P<h>\d{2,}):(?P<m>\d{2}):(?P<s>\d{2}),(?P<ms>\d{3})"
)
LANGUAGE_ALIASES = {
    "ko": {"ko", "kor"},
    "kor": {"ko", "kor"},
    "en": {"en", "eng"},
    "eng": {"en", "eng"},
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--transcription-id", required=True)
    parser.add_argument("--external-task-id")
    parser.add_argument("--provider-trace-id")
    parser.add_argument("--provider-request-id")
    parser.add_argument("--expected-cache-key")
    parser.add_argument("--expected-json-sha256")
    parser.add_argument("--expected-srt-sha256")
    parser.add_argument("--duration-tolerance-seconds", type=float, default=2.0)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Upload artifacts and conditionally publish the failed cache row.",
    )
    parser.add_argument(
        "--activate-project",
        action="store_true",
        help="Create a linked pending attempt and move a failed project to queued.",
    )
    return parser.parse_args()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _expected_duration(project: dict[str, Any]) -> float:
    source_derived = project.get("source_derived")
    if isinstance(source_derived, dict):
        duration_ms = _optional_float(source_derived.get("duration_ms"))
        if duration_ms and duration_ms > 0:
            return duration_ms / 1000.0
    duration = _optional_float(project.get("source_duration_seconds"))
    if duration and duration > 0:
        return duration
    raise RuntimeError("project has no usable source duration")


def _language_matches(requested: str, actual: str) -> bool:
    requested_hint = scribe_v2_cache.language_hint(requested)
    actual = actual.lower().strip()
    if requested_hint is None:
        return bool(actual)
    requested = requested_hint.lower()
    return actual in LANGUAGE_ALIASES.get(requested, {requested})


def _srt_seconds(timestamp: str) -> float:
    match = SRT_TIMESTAMP_RE.fullmatch(timestamp)
    if not match:
        raise RuntimeError(f"invalid SRT timestamp: {timestamp!r}")
    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
        + int(match.group("ms")) / 1000.0
    )


def _validate_srt(raw_srt: str, *, expected_duration: float, tolerance: float) -> dict[str, Any]:
    cue_headers = re.findall(
        r"(?m)^(\d+)\n(\d{2,}:\d{2}:\d{2},\d{3}) --> "
        r"(\d{2,}:\d{2}:\d{2},\d{3})$",
        raw_srt,
    )
    if not cue_headers:
        raise RuntimeError("recovered raw SRT contains no cues")

    previous_start = -1.0
    maximum_end = 0.0
    for expected_index, (index, start_text, end_text) in enumerate(cue_headers, 1):
        if int(index) != expected_index:
            raise RuntimeError(f"raw SRT cue index mismatch at {expected_index}: {index}")
        start = _srt_seconds(start_text)
        end = _srt_seconds(end_text)
        if end <= start:
            raise RuntimeError(f"raw SRT cue {index} has a non-positive duration")
        if start + 0.001 < previous_start:
            raise RuntimeError(f"raw SRT cue {index} is out of order")
        previous_start = start
        maximum_end = max(maximum_end, end)

    # Diarized speakers may legitimately overlap, so ordering is based on cue
    # starts rather than requiring every previous cue to have ended.
    if maximum_end > expected_duration + tolerance:
        raise RuntimeError(
            f"raw SRT ends after source duration: end={maximum_end} source={expected_duration}"
        )
    return {"cue_count": len(cue_headers), "last_end_seconds": maximum_end}


def _validate_transcript(
    payload: dict[str, Any],
    *,
    project: dict[str, Any],
    transcription_id: str,
    tolerance: float,
) -> dict[str, Any]:
    payload_id = str(payload.get("transcription_id") or "")
    if payload_id and payload_id != transcription_id:
        raise RuntimeError(
            f"transcription id mismatch: expected={transcription_id} actual={payload_id}"
        )

    actual_language = str(payload.get("language_code") or "")
    requested_language = str(project.get("language") or "")
    if not actual_language or not _language_matches(requested_language, actual_language):
        raise RuntimeError(
            f"transcript language mismatch: requested={requested_language} actual={actual_language}"
        )

    expected_duration = _expected_duration(project)
    actual_duration = _optional_float(payload.get("audio_duration_secs"))
    if actual_duration is None or actual_duration <= 0:
        raise RuntimeError("transcript is missing audio_duration_secs")
    if abs(actual_duration - expected_duration) > tolerance:
        raise RuntimeError(
            "transcript duration mismatch: "
            f"source={expected_duration} provider={actual_duration} tolerance={tolerance}"
        )

    raw_words = payload.get("words")
    if not isinstance(raw_words, list):
        raise RuntimeError("transcript words is not a list")
    words = [
        item
        for item in raw_words
        if isinstance(item, dict)
        and item.get("type", "word") == "word"
        and _optional_float(item.get("start")) is not None
        and _optional_float(item.get("end")) is not None
    ]
    if not words:
        raise RuntimeError("transcript contains no timed words")

    for item in words:
        start = float(item["start"])
        end = float(item["end"])
        if end < start:
            raise RuntimeError(f"transcript word has invalid timing: start={start} end={end}")

    last_word_end = max(float(item["end"]) for item in words)
    if last_word_end > actual_duration + tolerance:
        raise RuntimeError(
            f"last word exceeds transcript duration: end={last_word_end} duration={actual_duration}"
        )

    speakers = sorted(
        {
            str(item["speaker_id"])
            for item in words
            if item.get("speaker_id") not in (None, "")
        }
    )
    settings_value = project.get("settings") or {}
    expected_speakers = _optional_int(settings_value.get("num_speakers"))
    if settings_value.get("diarize", True):
        if not speakers:
            raise RuntimeError("diarized transcript contains no speaker IDs")
        if expected_speakers is not None and len(speakers) != expected_speakers:
            raise RuntimeError(
                f"speaker count mismatch: expected={expected_speakers} actual={len(speakers)}"
            )

    if not str(payload.get("text") or "").strip():
        raise RuntimeError("transcript text is empty")

    return {
        "provider_duration_seconds": actual_duration,
        "source_duration_seconds": expected_duration,
        "word_entry_count": len(raw_words),
        "timed_word_count": len(words),
        "speaker_ids": speakers,
        "last_word_end_seconds": last_word_end,
    }


def _load_project_and_cache(db, project_id: str) -> tuple[dict[str, Any], str, dict[str, Any]]:
    project = (
        db.table("projects")
        .select("*")
        .eq("id", project_id)
        .single()
        .execute()
        .data
    )
    if not project:
        raise RuntimeError(f"project not found: {project_id}")

    project_settings = project.get("settings") or {}
    source_sha256 = str(project.get("source_sha256") or "")
    source_size_bytes = _optional_int(project.get("source_size_bytes"))
    if not source_sha256 or source_size_bytes is None:
        raise RuntimeError("project source identity is incomplete")

    params = scribe_v2_cache.ScribeV2CacheParams(
        source_sha256=source_sha256,
        source_size_bytes=source_size_bytes,
        language=scribe_v2_cache.cache_language(project.get("language")),
        diarize=bool(project_settings.get("diarize", True)),
        num_speakers=_optional_int(project_settings.get("num_speakers")),
        tag_audio_events=bool(project_settings.get("tag_audio_events", True)),
    )
    cache_key = scribe_v2_cache.build_scribe_v2_cache_key(params)
    entry = scribe_v2_cache.get_cache_entry(db, cache_key)
    if not entry:
        raise RuntimeError(f"Scribe cache entry not found: {cache_key}")
    return project, cache_key, entry


def _verify_expected_hash(label: str, actual: str, expected: str | None) -> None:
    if expected and actual.lower() != expected.lower():
        raise RuntimeError(f"{label} SHA-256 mismatch: expected={expected} actual={actual}")


def _upload_and_verify(
    *,
    cache_key: str,
    raw_json_path: Path,
    raw_srt_path: Path,
) -> tuple[str, str, dict[str, str]]:
    raw_json_key = scribe_v2_cache.raw_json_r2_key(cache_key)
    raw_srt_key = scribe_v2_cache.raw_srt_r2_key(cache_key)
    r2.upload_file(str(raw_json_path), raw_json_key, "application/json")
    r2.upload_file(str(raw_srt_path), raw_srt_key, "text/plain")

    hashes = _verify_remote_artifacts(
        raw_json_key=raw_json_key,
        raw_srt_key=raw_srt_key,
        raw_json_path=raw_json_path,
        raw_srt_path=raw_srt_path,
    )
    return raw_json_key, raw_srt_key, hashes


def _verify_remote_artifacts(
    *,
    raw_json_key: str,
    raw_srt_key: str,
    raw_json_path: Path,
    raw_srt_path: Path,
) -> dict[str, str]:
    """Download both R2 objects and compare their exact normalized bytes."""
    local_json_sha = _sha256_file(raw_json_path)
    local_srt_sha = _sha256_file(raw_srt_path)

    remote_json = r2.download_to_bytes(raw_json_key)
    remote_srt = r2.download_to_bytes(raw_srt_key)
    remote_json_sha = _sha256_bytes(remote_json)
    remote_srt_sha = _sha256_bytes(remote_srt)
    if remote_json_sha != local_json_sha or remote_srt_sha != local_srt_sha:
        raise RuntimeError(
            "R2 verification failed: "
            f"json={local_json_sha}/{remote_json_sha} srt={local_srt_sha}/{remote_srt_sha}"
        )
    parsed = json.loads(remote_json.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise RuntimeError("R2 raw JSON is not an object after round trip")
    remote_srt.decode("utf-8")
    return {
        "raw_json_sha256": local_json_sha,
        "raw_srt_sha256": local_srt_sha,
    }


def _verify_completed_cache_artifacts(
    *,
    cache_key: str,
    entry: dict[str, Any],
    raw_json_path: Path,
    raw_srt_path: Path,
) -> tuple[str, str, dict[str, str]]:
    """Make an already-completed recovery rerun read-only and idempotent."""
    expected_json_key = scribe_v2_cache.raw_json_r2_key(cache_key)
    expected_srt_key = scribe_v2_cache.raw_srt_r2_key(cache_key)
    raw_json_key = entry.get("raw_json_r2_key")
    raw_srt_key = entry.get("raw_srt_r2_key")
    if raw_json_key != expected_json_key or raw_srt_key != expected_srt_key:
        raise RuntimeError(
            "completed cache artifact keys differ from the deterministic recovery keys"
        )
    hashes = _verify_remote_artifacts(
        raw_json_key=raw_json_key,
        raw_srt_key=raw_srt_key,
        raw_json_path=raw_json_path,
        raw_srt_path=raw_srt_path,
    )
    return raw_json_key, raw_srt_key, hashes


def _find_active_initial_job(db, *, project: dict[str, Any]) -> dict[str, Any] | None:
    active_jobs = (
        db.table("jobs")
        .select("id, type, status, retry_of_job_id, attempt_number, created_at")
        .eq("project_id", project["id"])
        .in_("status", ["queued", "pending", "running", "cancel_requested"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return active_jobs[0] if active_jobs else None


def _activate_project(db, *, project: dict[str, Any]) -> dict[str, Any]:
    project_id = project["id"]
    active_job = _find_active_initial_job(db, project=project)
    project_status = project.get("status")
    if active_job and active_job.get("type") != project.get("cut_type"):
        raise RuntimeError(f"project already has an unrelated active job: {active_job['id']}")
    if active_job and project_status in {"queued", "processing"}:
        # A previous invocation may have activated the linked job and exited
        # before printing its summary. Finish report cleanup without creating a
        # duplicate attempt or re-checking already-held credits.
        db.table("edit_reports").delete().eq("project_id", project_id).execute()
        return {**active_job, "already_active": True}
    if active_job and active_job.get("status") in {"queued", "running"}:
        latest_project = (
            db.table("projects")
            .select("status")
            .eq("id", project_id)
            .single()
            .execute()
            .data
        )
        if latest_project and latest_project.get("status") in {"queued", "processing"}:
            db.table("edit_reports").delete().eq("project_id", project_id).execute()
            return {**active_job, "already_active": True}
    if active_job and not (project_status == "failed" and active_job.get("status") == "pending"):
        raise RuntimeError(f"project already has active job: {active_job['id']}")
    if project_status != "failed":
        raise RuntimeError(f"project is not failed: status={project.get('status')}")

    duration = int(project.get("source_duration_seconds") or 0)
    balance = get_balance(project["user_id"])
    if duration <= 0 or int(balance.get("available_seconds") or 0) < duration:
        raise RuntimeError(
            f"insufficient credits for retry: required={duration} "
            f"available={balance.get('available_seconds')}"
        )

    created_new_job = active_job is None
    if active_job is not None:
        # Crash-safe continuation: the linked pending attempt was durably
        # created, but the project status was not changed yet.
        job = active_job
    else:
        previous_jobs = (
            db.table("jobs")
            .select("id, attempt_number")
            .eq("project_id", project_id)
            .eq("user_id", project["user_id"])
            .eq("type", project["cut_type"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        previous_job = previous_jobs[0] if previous_jobs else None
        previous_attempt = int((previous_job or {}).get("attempt_number") or (1 if previous_job else 0))
        try:
            job = create_initial_job(
                db,
                project,
                retry_of_job_id=(previous_job or {}).get("id"),
                attempt_number=previous_attempt + 1,
            )
        except Exception:
            winner = _find_active_initial_job(db, project=project)
            if not (
                winner
                and winner.get("type") == project["cut_type"]
                and winner.get("status") in {"queued", "pending", "running"}
            ):
                raise
            job = winner
            created_new_job = False

    activated = (
        db.table("projects")
        .update({"status": "queued"})
        .eq("id", project_id)
        .eq("status", "failed")
        .execute()
        .data
    )
    if not activated:
        latest_project = (
            db.table("projects")
            .select("status")
            .eq("id", project_id)
            .single()
            .execute()
            .data
        )
        if latest_project and latest_project.get("status") in {"queued", "processing"}:
            db.table("edit_reports").delete().eq("project_id", project_id).execute()
            return {**job, "already_active": True}
        if created_new_job:
            db.table("jobs").update({
                "status": "failed",
                "error_message": "Recovery activation lost project status race",
                "completed_at": "now()",
            }).eq("id", job["id"]).eq("status", "pending").execute()
        raise RuntimeError("project activation lost a status race; pending job was not activated")
    db.table("edit_reports").delete().eq("project_id", project_id).execute()
    return {**job, "already_active": not created_new_job}


def main() -> int:
    args = _parse_args()
    if args.activate_project and not args.apply:
        raise RuntimeError("--activate-project requires --apply")
    if args.duration_tolerance_seconds < 0:
        raise RuntimeError("--duration-tolerance-seconds must be non-negative")

    db = get_db()
    project, cache_key, entry = _load_project_and_cache(db, args.project_id)
    if args.expected_cache_key and args.expected_cache_key != cache_key:
        raise RuntimeError(
            f"cache key mismatch: expected={args.expected_cache_key} actual={cache_key}"
        )
    if entry.get("status") not in {"failed", "completed"}:
        raise RuntimeError(f"cache is not recoverable: status={entry.get('status')}")
    cached_transcription_id = entry.get("provider_transcription_id")
    if cached_transcription_id and cached_transcription_id != args.transcription_id:
        raise RuntimeError(
            "cache provider transcription mismatch: "
            f"expected={cached_transcription_id} requested={args.transcription_id}"
        )

    with tempfile.TemporaryDirectory(prefix="eogum-scribe-recovery-") as temp_name:
        result = chalna.recover_provider_transcript_to_files(
            args.transcription_id,
            output_dir=temp_name,
        )
        if result is None:
            raise RuntimeError(f"provider transcript not found: {args.transcription_id}")
        raw_json_path = Path(result.raw_json_path)
        raw_srt_path = Path(result.raw_srt_path)
        payload = json.loads(raw_json_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("recovered transcript is not a JSON object")

        transcript_stats = _validate_transcript(
            payload,
            project=project,
            transcription_id=args.transcription_id,
            tolerance=args.duration_tolerance_seconds,
        )
        srt_stats = _validate_srt(
            raw_srt_path.read_text(encoding="utf-8"),
            expected_duration=transcript_stats["provider_duration_seconds"],
            tolerance=args.duration_tolerance_seconds,
        )
        json_sha = _sha256_file(raw_json_path)
        srt_sha = _sha256_file(raw_srt_path)
        _verify_expected_hash("raw JSON", json_sha, args.expected_json_sha256)
        _verify_expected_hash("raw SRT", srt_sha, args.expected_srt_sha256)

        summary: dict[str, Any] = {
            "project_id": args.project_id,
            "cache_key": cache_key,
            "cache_status_before": entry.get("status"),
            "transcription_id": args.transcription_id,
            "raw_json_sha256": json_sha,
            "raw_srt_sha256": srt_sha,
            **transcript_stats,
            **srt_stats,
            "applied": False,
            "project_activated": False,
        }
        if not args.apply:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        latest = scribe_v2_cache.get_cache_entry(db, cache_key) or entry
        if latest.get("status") == "completed":
            # A repeated command must never overwrite objects already referenced
            # by a completed cache row. It only proves their exact byte identity.
            raw_json_key, raw_srt_key, r2_hashes = _verify_completed_cache_artifacts(
                cache_key=cache_key,
                entry=latest,
                raw_json_path=raw_json_path,
                raw_srt_path=raw_srt_path,
            )
        elif latest.get("status") == "failed":
            raw_json_key, raw_srt_key, r2_hashes = _upload_and_verify(
                cache_key=cache_key,
                raw_json_path=raw_json_path,
                raw_srt_path=raw_srt_path,
            )
            recovered = scribe_v2_cache.recover_failed_cache_as_completed(
                db,
                cache_key=cache_key,
                raw_json_key=raw_json_key,
                raw_srt_key=raw_srt_key,
                external_task_id=args.external_task_id or entry.get("external_task_id"),
                provider_request_id=(
                    args.provider_request_id
                    or result.provider_request_id
                    or entry.get("provider_request_id")
                ),
                provider_transcription_id=args.transcription_id,
                provider_trace_id=(
                    args.provider_trace_id
                    or result.provider_trace_id
                    or entry.get("provider_trace_id")
                ),
                attempt_count=max(1, int(entry.get("attempt_count") or 0)),
                expected_owner_token=entry.get("owner_token"),
                expected_attempt_count=int(entry.get("attempt_count") or 0),
            )
            if recovered is None:
                latest = scribe_v2_cache.get_cache_entry(db, cache_key)
                if not latest or latest.get("status") != "completed":
                    raise RuntimeError("failed cache changed concurrently; artifacts were not published")
                _verify_completed_cache_artifacts(
                    cache_key=cache_key,
                    entry=latest,
                    raw_json_path=raw_json_path,
                    raw_srt_path=raw_srt_path,
                )
        else:
            raise RuntimeError(f"cache changed while recovering: status={latest.get('status')}")
        summary.update(r2_hashes)
        summary["applied"] = True
        summary["raw_json_r2_key"] = raw_json_key
        summary["raw_srt_r2_key"] = raw_srt_key

        if args.activate_project:
            fresh_project = (
                db.table("projects")
                .select("*")
                .eq("id", args.project_id)
                .single()
                .execute()
                .data
            )
            job = _activate_project(db, project=fresh_project)
            summary["project_activated"] = True
            summary["retry_job_id"] = job["id"]
            summary["attempt_number"] = job.get("attempt_number")
            summary["retry_of_job_id"] = job.get("retry_of_job_id")
            summary["activation_already_applied"] = bool(job.get("already_active"))

        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
