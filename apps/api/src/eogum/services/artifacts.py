"""Helpers for locating durable project artifact jobs."""

from typing import Any

from eogum.services.database import execute_with_retry

ARTIFACT_JOB_TYPES = ["subtitle_cut", "podcast_cut", "reprocess_multicam", "cut_decision"]


def get_latest_artifact_job(
    db: Any,
    project_id: str,
    *,
    user_id: str | None = None,
    select: str = "id, result_r2_keys, type, created_at",
) -> dict | None:
    """Return the latest completed job that owns canonical project artifacts.

    Jobs such as ``final_preview`` also produce R2 objects, but they must not
    become the source of truth for downloads, review segments, or reprocess.
    """
    query = (
        db.table("jobs")
        .select(select)
        .eq("project_id", project_id)
        .eq("status", "completed")
        .in_("type", ARTIFACT_JOB_TYPES)
    )
    if user_id is not None:
        query = query.eq("user_id", user_id)
    query = query.order("created_at", desc=True).limit(1)
    result = execute_with_retry(
        lambda: query.execute(),
        operation_name=f"jobs.select.latest_artifact project_id={project_id}",
    )
    row = result.data[0] if result.data else None
    if not row or not row.get("result_r2_keys"):
        return None
    return row
