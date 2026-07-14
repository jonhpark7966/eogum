"""AI-only main-source render identity."""

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
WEB_RENDER_PROFILE = "web_1080p_v2"
RENDER_VERSION = 3
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
