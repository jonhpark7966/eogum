"""AI-only main-source render identity and interval planning."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from eogum.services.database import execute_with_retry

AI_SOURCE_JOB_TYPES = [
    "subtitle_cut",
    "podcast_cut",
    "ai_frontier_cut",
    "cut_decision",
]
AI_CUT_RENDER_TYPE = "ai_cut_render"
AI_DECISION_MODE = "ai"
WEB_RENDER_PROFILE = "web_1080p_v1"
RENDER_VERSION = 1
REUSABLE_RENDER_STATUSES = ["pending", "queued", "running", "completed"]


def get_latest_ai_source_job(
    db: Any,
    project_id: str,
    *,
    user_id: str | None = None,
    select: str = "id,result_r2_keys,type,created_at",
) -> dict | None:
    """Return the latest completed AI decision job that has project JSON.

    Multicam reprocessing is intentionally not eligible: its project JSON may
    contain human evaluation changes and extra-source switching decisions.
    """
    query = (
        db.table("jobs")
        .select(select)
        .eq("project_id", project_id)
        .eq("status", "completed")
        .in_("type", AI_SOURCE_JOB_TYPES)
        .order("created_at", desc=True)
    )
    if user_id is not None:
        query = query.eq("user_id", user_id)
    result = execute_with_retry(
        lambda: query.execute(),
        operation_name=f"jobs.select.latest_ai_source project_id={project_id}",
    )
    for row in result.data or []:
        if (row.get("result_r2_keys") or {}).get("project_json"):
            return row
    return None


def render_identity(project: dict, source_job: dict) -> dict:
    result_keys = source_job.get("result_r2_keys") or {}
    return {
        "decision_mode": AI_DECISION_MODE,
        "project_json_r2_key": result_keys.get("project_json"),
        "render_profile": WEB_RENDER_PROFILE,
        "render_version": RENDER_VERSION,
        "source_job_id": source_job.get("id"),
        "source_sha256": project.get("source_sha256"),
    }


def render_dedupe_key(project: dict, source_job: dict) -> str:
    canonical = json.dumps(
        render_identity(project, source_job),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def output_r2_key(project_id: str, dedupe_key: str) -> str:
    return f"results/{project_id}/renders/{dedupe_key}/main-source-ai-cut.mp4"


def _int_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def primary_video_track(project_data: dict) -> dict | None:
    """Find the video track backed by source_files[0], never an extra source."""
    source_files = project_data.get("source_files") or []
    if not source_files:
        return None
    primary_source_id = source_files[0].get("id")
    if primary_source_id is None:
        return None
    for track in project_data.get("tracks") or []:
        if track.get("track_type") == "video" and track.get("source_file_id") == primary_source_id:
            return track
    return None


def _merge_clamped_ranges(ranges: list[tuple[int, int]], total_duration_ms: int) -> list[tuple[int, int]]:
    clamped = sorted(
        (max(0, start), min(total_duration_ms, end))
        for start, end in ranges
        if end > start and end > 0 and start < total_duration_ms
    )
    if not clamped:
        return []

    merged: list[tuple[int, int]] = []
    current_start, current_end = clamped[0]
    for start, end in clamped[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            merged.append((current_start, current_end))
            current_start, current_end = start, end
    merged.append((current_start, current_end))
    return merged


def keep_ranges_ms(project_data: dict, total_duration_ms: int) -> list[tuple[int, int]]:
    """Invert explicit primary-track cut/mute decisions over the probed source.

    This deliberately does not use transcription segments. Non-transcribed
    intros, outros, and silence without an explicit removal decision remain.
    """
    if total_duration_ms <= 0:
        return []
    track = primary_video_track(project_data)
    track_id = str((track or {}).get("id") or "")
    if not track_id:
        return []

    removed: list[tuple[int, int]] = []
    for decision in project_data.get("edit_decisions") or []:
        if decision.get("active_video_track_id") != track_id:
            continue
        if decision.get("edit_type") not in {"cut", "mute"}:
            continue
        range_data = decision.get("range") or {}
        start_ms = _int_value(range_data.get("start_ms"))
        end_ms = _int_value(range_data.get("end_ms"))
        if start_ms is None or end_ms is None or end_ms <= start_ms:
            continue
        removed.append((start_ms, end_ms))

    keep: list[tuple[int, int]] = []
    cursor = 0
    for start_ms, end_ms in _merge_clamped_ranges(removed, total_duration_ms):
        if start_ms > cursor:
            keep.append((cursor, start_ms))
        cursor = max(cursor, end_ms)
    if cursor < total_duration_ms:
        keep.append((cursor, total_duration_ms))
    return keep


def keep_intervals(project_data: dict, total_duration_ms: int) -> list[tuple[float, float]]:
    return [
        (start_ms / 1000.0, (end_ms - start_ms) / 1000.0)
        for start_ms, end_ms in keep_ranges_ms(project_data, total_duration_ms)
        if end_ms > start_ms
    ]
