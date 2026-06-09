import json
import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from eogum.auth import get_user_id
from eogum.models.schemas import (
    ProjectCreate,
    ProjectDetailResponse,
    ProjectResponse,
    UpdateExtraSourcesRequest,
)
from eogum.services.artifacts import get_latest_artifact_job
from eogum.services.credit import get_balance
from eogum.services.database import get_db
from eogum.services.job_runner import enqueue, enqueue_reprocess
from eogum.services.r2 import delete_objects, download_to_bytes

router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger(__name__)

ALLOWED_TARGET_DURATION_MINUTES = {20, 40, 60}


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


def _pending_multicam_state(project: dict, extra_sources: list[dict]) -> dict:
    current = project.get("multicam_state") or {}
    desired_hash = _extra_sources_hash(extra_sources)
    if not desired_hash:
        return {
            "status": "not_applied",
            "desired_sources_hash": None,
            "applied_sources_hash": None,
            "source_count": 0,
            "job_id": None,
            "applied_at": None,
            "error": None,
        }

    applied_hash = current.get("applied_sources_hash")
    status_value = "applied" if applied_hash == desired_hash else "pending_apply"
    return {
        **current,
        "status": status_value,
        "desired_sources_hash": desired_hash,
        "source_count": len(extra_sources),
        "error": None,
    }


def _validate_project_settings(req: ProjectCreate) -> None:
    settings_value = req.settings or {}
    target = settings_value.get("output_target_duration_minutes")
    if target is None:
        target_minutes = None
    else:
        if isinstance(target, bool):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="결과 길이는 20, 40, 60분 중 하나여야 합니다",
            )

        try:
            target_minutes = int(target)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="결과 길이는 20, 40, 60분 중 하나여야 합니다",
            ) from None

        if target_minutes not in ALLOWED_TARGET_DURATION_MINUTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="결과 길이는 20, 40, 60분 중 하나여야 합니다",
            )

        min_source_seconds = int(target_minutes * 60 * 0.9)
        if req.source_duration_seconds < min_source_seconds:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{target_minutes}분 결과물을 만들려면 원본이 최소 {min_source_seconds}초 이상이어야 합니다",
            )

    for key in ("diarize", "tag_audio_events", "use_llm_segmentation", "use_llm_refinement"):
        value = settings_value.get(key)
        if value is not None and not isinstance(value, bool):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{key} 옵션은 true 또는 false여야 합니다",
            )

    num_speakers = settings_value.get("num_speakers")
    if num_speakers in (None, ""):
        return
    if isinstance(num_speakers, bool):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="예상 화자 수는 1에서 32 사이 숫자여야 합니다",
        )
    try:
        speaker_count = int(num_speakers)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="예상 화자 수는 1에서 32 사이 숫자여야 합니다",
        ) from None
    if not 1 <= speaker_count <= 32:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="예상 화자 수는 1에서 32 사이 숫자여야 합니다",
        )


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(req: ProjectCreate, user_id: str = Depends(get_user_id)):
    _validate_project_settings(req)

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
    updated = (
        db.table("projects")
        .update({
            "extra_sources": extra_sources,
            "multicam_state": _pending_multicam_state(project.data, extra_sources),
        })
        .eq("id", project_id)
        .execute()
        .data[0]
    )
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
        .maybe_single()
        .execute()
    )
    if existing_reprocess.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 재처리 작업이 진행 중입니다",
        )

    job = get_latest_artifact_job(db, project_id, select="id, result_r2_keys")
    if not job:
        raise HTTPException(status_code=404, detail="완료된 작업이 없습니다. 전체 재처리가 필요합니다.")

    r2_keys = job["result_r2_keys"]
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

    desired_hash = _extra_sources_hash(project.data.get("extra_sources") or [])
    queued_job = db.table("jobs").insert({
        "project_id": project_id,
        "user_id": user_id,
        "type": "reprocess_multicam",
        "status": "pending",
        "progress": 0,
    }).execute().data[0]
    multicam_state = {
        **(project.data.get("multicam_state") or {}),
        "status": "queued",
        "desired_sources_hash": desired_hash,
        "source_count": len(project.data.get("extra_sources") or []),
        "job_id": queued_job["id"],
        "error": None,
    }
    db.table("projects").update({"status": "processing", "multicam_state": multicam_state}).eq("id", project_id).execute()
    enqueue_reprocess(project_id, queued_job["id"])

    return db.table("projects").select("*").eq("id", project_id).single().execute().data


@router.post("/{project_id}/multicam/cancel", response_model=ProjectResponse)
def cancel_multicam_reprocess(project_id: str, user_id: str = Depends(get_user_id)):
    db = get_db()

    project = db.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    latest = (
        db.table("jobs")
        .select("id, status")
        .eq("project_id", project_id)
        .eq("user_id", user_id)
        .eq("type", "reprocess_multicam")
        .in_("status", ["pending", "running", "cancel_requested"])
        .order("created_at", desc=True)
        .limit(1)
        .maybe_single()
        .execute()
    )
    if not latest.data:
        raise HTTPException(status_code=404, detail="취소할 멀티캠 작업이 없습니다")

    job_status = latest.data["status"]
    job_id = latest.data["id"]
    next_job_status = "canceled" if job_status == "pending" else "cancel_requested"
    job_update = {"status": next_job_status}
    if next_job_status == "canceled":
        job_update.update({"progress": 0, "completed_at": "now()"})
    db.table("jobs").update(job_update).eq("id", job_id).execute()

    state_status = "canceled" if next_job_status == "canceled" else "canceling"
    multicam_state = {
        **(project.data.get("multicam_state") or {}),
        "status": state_status,
        "job_id": job_id,
        "error": None,
    }
    updated = (
        db.table("projects")
        .update({"status": "completed", "multicam_state": multicam_state})
        .eq("id", project_id)
        .execute()
        .data[0]
    )
    return updated


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, user_id: str = Depends(get_user_id)):
    db = get_db()

    project = db.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    if project.data["status"] in {"queued", "processing"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="처리 중인 프로젝트는 완료 또는 취소 후 삭제할 수 있습니다",
        )

    active_job = (
        db.table("jobs")
        .select("id")
        .eq("project_id", project_id)
        .in_("status", ["pending", "running", "cancel_requested"])
        .limit(1)
        .execute()
    )
    if active_job.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="진행 중인 작업이 있어 프로젝트를 삭제할 수 없습니다",
        )

    r2_keys = [project.data.get("source_r2_key")]
    r2_keys.extend(src.get("r2_key") for src in (project.data.get("extra_sources") or []))
    jobs = db.table("jobs").select("result_r2_keys").eq("project_id", project_id).execute()
    for job in jobs.data or []:
        for key in (job.get("result_r2_keys") or {}).values():
            if isinstance(key, str):
                r2_keys.append(key)
    try:
        delete_objects([key for key in r2_keys if key])
    except Exception:
        logger.exception("Best-effort R2 cleanup failed for project %s", project_id)

    db.table("projects").delete().eq("id", project_id).execute()
