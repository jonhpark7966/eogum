"""Durable AI-only main-source MP4 render endpoints."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status

from eogum.auth import CurrentUser, get_current_user
from eogum.models.schemas import (
    AiCutRenderJobResponse,
    AiCutRenderLatestResponse,
    DownloadResponse,
)
from eogum.services import ai_cut_render, r2
from eogum.services.database import get_db
from eogum.services.job_runner import enqueue_ai_cut_render


router = APIRouter(prefix="/projects/{project_id}/renders", tags=["renders"])


def _first_row(result) -> dict | None:
    data = getattr(result, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None


def _get_project(db, project_id: str, current_user: CurrentUser, select: str = "*") -> dict:
    query = db.table("projects").select(select).eq("id", project_id)
    if not current_user.is_admin:
        query = query.eq("user_id", current_user.id)
    project = query.single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")
    return project.data


def _render_response(job: dict) -> AiCutRenderJobResponse:
    metadata = job.get("processing_metadata") or {}
    result_keys = job.get("result_r2_keys") or {}
    return AiCutRenderJobResponse(
        job_id=job["id"],
        status=job["status"],
        progress=int(job.get("progress") or 0),
        error_message=job.get("error_message"),
        source_job_id=job.get("source_job_id"),
        render_profile=str(metadata.get("render_profile") or ai_cut_render.WEB_RENDER_PROFILE),
        duration_ms=metadata.get("duration_ms"),
        size_bytes=metadata.get("size_bytes"),
        download_ready=job.get("status") == "completed" and bool(result_keys.get("video")),
        created_at=job["created_at"],
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
    )


def _render_jobs(db, project_id: str) -> list[dict]:
    return (
        db.table("jobs")
        .select("*")
        .eq("project_id", project_id)
        .eq("type", ai_cut_render.AI_CUT_RENDER_TYPE)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )


def _find_reusable_job(db, project_id: str, dedupe_key: str) -> dict | None:
    result = (
        db.table("jobs")
        .select("*")
        .eq("project_id", project_id)
        .eq("type", ai_cut_render.AI_CUT_RENDER_TYPE)
        .eq("dedupe_key", dedupe_key)
        .in_("status", ai_cut_render.REUSABLE_RENDER_STATUSES)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return _first_row(result)


def _current_source(db, project: dict) -> tuple[dict | None, str | None]:
    source_job = ai_cut_render.get_latest_ai_source_job(
        db,
        project["id"],
        user_id=project["user_id"],
    )
    if not source_job:
        return None, None
    return source_job, ai_cut_render.render_dedupe_key(project, source_job)


def _assert_renderable_project(db, project: dict) -> tuple[dict, str]:
    if project.get("status") != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="완료된 프로젝트만 AI 컷편집 영상을 생성할 수 있습니다",
        )
    source_r2_key = project.get("source_r2_key")
    if not source_r2_key:
        raise HTTPException(status_code=409, detail="메인 소스가 없습니다")
    try:
        source_exists = r2.object_exists(source_r2_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="메인 소스 상태를 확인할 수 없습니다") from exc
    if not source_exists:
        raise HTTPException(status_code=409, detail="저장된 메인 소스 파일이 없습니다")

    source_job, dedupe_key = _current_source(db, project)
    if not source_job or not dedupe_key:
        raise HTTPException(status_code=409, detail="AI 프로젝트 JSON이 있는 완료 작업이 없습니다")
    return source_job, dedupe_key


@router.post("/ai-cut", response_model=AiCutRenderJobResponse)
def start_ai_cut_render(
    project_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = get_db()
    project = _get_project(db, project_id, current_user)
    source_job, dedupe_key = _assert_renderable_project(db, project)

    reusable = _find_reusable_job(db, project_id, dedupe_key)
    if reusable:
        return _render_response(reusable)

    payload = {
        "project_id": project_id,
        "user_id": project["user_id"],
        "type": ai_cut_render.AI_CUT_RENDER_TYPE,
        "status": "pending",
        "progress": 0,
        "source_job_id": source_job["id"],
        "dedupe_key": dedupe_key,
        "processing_metadata": {
            "decision_mode": ai_cut_render.AI_DECISION_MODE,
            "render_profile": ai_cut_render.WEB_RENDER_PROFILE,
            "render_version": ai_cut_render.RENDER_VERSION,
        },
    }
    try:
        job = db.table("jobs").insert(payload).execute().data[0]
    except Exception:
        # The partial unique index is the final arbiter for concurrent POSTs.
        reusable = _find_reusable_job(db, project_id, dedupe_key)
        if not reusable:
            raise
        return _render_response(reusable)

    enqueue_ai_cut_render(project_id, job["id"])
    return _render_response(job)


@router.get("/ai-cut/latest", response_model=AiCutRenderLatestResponse)
def get_latest_ai_cut_render(
    project_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = get_db()
    project = _get_project(db, project_id, current_user)
    source_job, current_dedupe = _current_source(db, project)
    jobs = _render_jobs(db, project_id)

    current_job = None
    if source_job and current_dedupe:
        current_job = next((job for job in jobs if job.get("dedupe_key") == current_dedupe), None)
    has_stale_render = any(
        job.get("status") == "completed"
        and (current_dedupe is None or job.get("dedupe_key") != current_dedupe)
        for job in jobs
    )
    return AiCutRenderLatestResponse(
        current_job=_render_response(current_job) if current_job else None,
        has_stale_render=has_stale_render,
    )


def _get_render_job(db, project_id: str, job_id: str) -> dict:
    result = (
        db.table("jobs")
        .select("*")
        .eq("id", job_id)
        .eq("project_id", project_id)
        .eq("type", ai_cut_render.AI_CUT_RENDER_TYPE)
        .maybe_single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="렌더 작업을 찾을 수 없습니다")
    return result.data


@router.get("/{job_id}", response_model=AiCutRenderJobResponse)
def get_ai_cut_render(
    project_id: str,
    job_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = get_db()
    _get_project(db, project_id, current_user, "id,user_id")
    return _render_response(_get_render_job(db, project_id, job_id))


def _safe_project_filename(name: str) -> str:
    sanitized = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', "_", name).strip(" ._")
    sanitized = re.sub(r"\s+", " ", sanitized)
    return (sanitized or "project")[:100]


@router.get("/{job_id}/download", response_model=DownloadResponse)
def download_ai_cut_render(
    project_id: str,
    job_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = get_db()
    project = _get_project(db, project_id, current_user, "id,user_id,name")
    job = _get_render_job(db, project_id, job_id)
    video_key = (job.get("result_r2_keys") or {}).get("video")
    if job.get("status") != "completed" or not video_key:
        raise HTTPException(status_code=409, detail="아직 다운로드할 수 없는 렌더 작업입니다")

    filename = f"{_safe_project_filename(project['name'])}_AI-cut.mp4"
    return DownloadResponse(
        download_url=r2.generate_presigned_download(video_key, filename),
        filename=filename,
    )
