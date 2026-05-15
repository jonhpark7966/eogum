import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from eogum.auth import get_user_id
from eogum.config import settings
from eogum.models.schemas import (
    ProjectCreate,
    ProjectDetailResponse,
    ProjectResponse,
    UpdateExtraSourcesRequest,
)
from eogum.services.credit import get_balance
from eogum.services.database import get_db
from eogum.services.job_runner import cancel_reprocess, enqueue, enqueue_reprocess
from eogum.services.r2 import download_to_bytes

router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger(__name__)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(req: ProjectCreate, user_id: str = Depends(get_user_id)):
    # Check credits
    balance = get_balance(user_id)
    if balance["available_seconds"] < req.source_duration_seconds:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"크레딧이 부족합니다. 필요: {req.source_duration_seconds}초, 사용 가능: {balance['available_seconds']}초",
        )

    db = get_db()
    project = db.table("projects").insert({
        "user_id": user_id,
        "name": req.name,
        "status": "queued",
        "cut_type": req.cut_type,
        "language": req.language,
        "source_r2_key": req.source_r2_key,
        "source_filename": req.source_filename,
        "source_duration_seconds": req.source_duration_seconds,
        "source_size_bytes": req.source_size_bytes,
        "settings": req.settings,
    }).execute().data[0]

    # Enqueue for processing
    enqueue(project["id"])

    return project


@router.get("", response_model=list[ProjectResponse])
def list_projects(user_id: str = Depends(get_user_id)):
    db = get_db()
    result = db.table("projects").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
    projects = result.data
    if not projects:
        return projects

    active_jobs_by_project: dict[str, dict] = {}
    active_project_ids = [
        project["id"]
        for project in projects
        if project["status"] in ("queued", "processing")
    ]

    if active_project_ids:
        try:
            jobs = (
                db.table("jobs")
                .select("id,project_id,type,status,progress,error_message,started_at,completed_at,created_at")
                .in_("project_id", active_project_ids)
                .in_("status", ["pending", "running"])
                .order("created_at", desc=True)
                .execute()
            )

            for job in jobs.data:
                active_jobs_by_project.setdefault(job["project_id"], job)
        except Exception:
            logger.exception("Failed to load active project jobs for dashboard list")

    for project in projects:
        project["active_job"] = active_jobs_by_project.get(project["id"])

    return projects


@router.get("/transcription-context")
def get_transcription_context(_user_id: str = Depends(get_user_id)):
    """Load curated transcription context from Chalna assets on demand."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{settings.chalna_url.rstrip('/')}/context/transcription")
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.exception("Failed to load transcription context from Chalna")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="전사 컨텍스트를 불러올 수 없습니다",
        ) from exc

    return {"context": str(payload.get("context") or "")}


@router.get("/{project_id}", response_model=ProjectDetailResponse)
def get_project(project_id: str, user_id: str = Depends(get_user_id)):
    db = get_db()

    project = db.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    jobs = db.table("jobs").select("*").eq("project_id", project_id).order("created_at").execute()
    try:
        report = db.table("edit_reports").select("*").eq("project_id", project_id).limit(1).execute()
        report_data = report.data[0] if report.data else None
    except Exception:
        logger.exception("Failed to load edit report for project %s", project_id)
        report_data = None

    data = project.data
    data["jobs"] = jobs.data
    data["report"] = report_data
    return data


@router.post("/{project_id}/retry", response_model=ProjectResponse)
def retry_project(project_id: str, user_id: str = Depends(get_user_id)):
    db = get_db()

    project = db.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    if project.data["status"] != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="실패한 프로젝트만 재시도할 수 있습니다",
        )

    # Check credits
    duration = project.data["source_duration_seconds"]
    balance = get_balance(user_id)
    if balance["available_seconds"] < duration:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"크레딧이 부족합니다. 필요: {duration}초, 사용 가능: {balance['available_seconds']}초",
        )

    # Clean up old failed jobs and reports
    db.table("jobs").delete().eq("project_id", project_id).execute()
    db.table("edit_reports").delete().eq("project_id", project_id).execute()

    # Reset project status
    updated = db.table("projects").update({"status": "queued"}).eq("id", project_id).execute().data[0]

    # Enqueue for processing
    enqueue(project_id)

    return updated


@router.put("/{project_id}/extra-sources", response_model=ProjectResponse)
def update_extra_sources(
    project_id: str,
    req: UpdateExtraSourcesRequest,
    user_id: str = Depends(get_user_id),
):
    db = get_db()

    project = db.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    if project.data["status"] not in ("completed", "failed", "reprocess_failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="완료 또는 실패한 프로젝트만 추가 소스를 설정할 수 있습니다",
        )

    extra_sources = [s.model_dump() for s in req.extra_sources]
    updated = db.table("projects").update({"extra_sources": extra_sources}).eq("id", project_id).execute().data[0]
    return updated


@router.post("/{project_id}/multicam", response_model=ProjectResponse)
def multicam_reprocess(project_id: str, user_id: str = Depends(get_user_id)):
    """Queue project reprocess via split avid-cli commands."""
    db = get_db()

    project = db.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    if project.data["status"] not in ("completed", "failed", "reprocess_failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="완료 또는 실패한 프로젝트만 재처리할 수 있습니다",
        )

    existing_reprocess = (
        db.table("jobs")
        .select("id")
        .eq("project_id", project_id)
        .eq("type", "reprocess_multicam")
        .in_("status", ["pending", "running"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if existing_reprocess.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 재처리 작업이 진행 중입니다",
        )

    job = (
        db.table("jobs")
        .select("id, result_r2_keys")
        .eq("project_id", project_id)
        .eq("status", "completed")
        .order("created_at", desc=True)
        .limit(1)
        .maybe_single()
        .execute()
    )
    if not job.data or not job.data.get("result_r2_keys"):
        raise HTTPException(status_code=404, detail="완료된 작업이 없습니다. 전체 재처리가 필요합니다.")

    r2_keys = job.data["result_r2_keys"]
    project_json_key = r2_keys.get("project_json")
    if not project_json_key:
        raise HTTPException(status_code=404, detail="프로젝트 JSON이 없습니다. 전체 재처리가 필요합니다.")

    project_json_bytes = download_to_bytes(project_json_key)
    try:
        stored_project_json = json.loads(project_json_bytes.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="저장된 프로젝트 JSON을 읽을 수 없습니다") from exc

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

    has_extra_sources = bool(project.data.get("extra_sources"))
    current_project_has_extra_sources = len(stored_project_json.get("source_files") or []) > 1
    if not eval_segments and not has_extra_sources and not current_project_has_extra_sources:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="평가 데이터 또는 적용할 extra source 변경이 필요합니다",
        )

    db.table("projects").update({"status": "processing"}).eq("id", project_id).execute()
    queued_job = db.table("jobs").insert({
        "project_id": project_id,
        "user_id": user_id,
        "type": "reprocess_multicam",
        "status": "pending",
        "progress": 0,
    }).execute().data[0]
    enqueue_reprocess(project_id, queued_job["id"])

    return db.table("projects").select("*").eq("id", project_id).single().execute().data


@router.post("/{project_id}/multicam/cancel", response_model=ProjectResponse)
def cancel_multicam_reprocess(project_id: str, user_id: str = Depends(get_user_id)):
    """Cancel a pending or running multicam reprocess job."""
    db = get_db()

    project = db.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    reprocess_job = (
        db.table("jobs")
        .select("id, status")
        .eq("project_id", project_id)
        .eq("type", "reprocess_multicam")
        .in_("status", ["pending", "running"])
        .order("created_at", desc=True)
        .limit(1)
        .maybe_single()
        .execute()
    )
    if not reprocess_job.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="취소할 멀티캠 적용 작업이 없습니다",
        )

    job_id = reprocess_job.data["id"]
    cancel_reprocess(project_id, job_id)
    db.table("jobs").update({
        "status": "canceled",
        "error_message": "멀티캠 적용이 취소되었습니다",
        "completed_at": "now()",
    }).eq("id", job_id).execute()
    db.table("projects").update({"status": "completed"}).eq("id", project_id).execute()

    return db.table("projects").select("*").eq("id", project_id).single().execute().data


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, user_id: str = Depends(get_user_id)):
    db = get_db()

    project = db.table("projects").select("id").eq("id", project_id).eq("user_id", user_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    db.table("projects").delete().eq("id", project_id).execute()
