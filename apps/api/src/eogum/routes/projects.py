from fastapi import APIRouter, Depends, HTTPException, status

from eogum.auth import get_user_id
from eogum.models.schemas import (
    ProjectCreate,
    ProjectDetailResponse,
    ProjectResponse,
)
from eogum.services.credit import get_balance
from eogum.services.database import get_db
from eogum.services.job_runner import enqueue

router = APIRouter(prefix="/projects", tags=["projects"])


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
    return result.data


@router.get("/{project_id}", response_model=ProjectDetailResponse)
def get_project(project_id: str, user_id: str = Depends(get_user_id)):
    db = get_db()

    project = db.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    jobs = db.table("jobs").select("*").eq("project_id", project_id).order("created_at").execute()
    report = db.table("edit_reports").select("*").eq("project_id", project_id).limit(1).execute()

    data = project.data
    data["jobs"] = jobs.data
    data["report"] = report.data[0] if report.data else None
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

    # Reset project status
    updated = db.table("projects").update({"status": "queued"}).eq("id", project_id).execute().data[0]

    # Enqueue for processing
    enqueue(project_id)

    return updated


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, user_id: str = Depends(get_user_id)):
    db = get_db()

    project = db.table("projects").select("id").eq("id", project_id).eq("user_id", user_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    db.table("projects").delete().eq("id", project_id).execute()
