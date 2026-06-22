from fastapi import APIRouter, Depends, HTTPException

from eogum.auth import CurrentUser, get_current_user
from eogum.models.schemas import DownloadResponse
from eogum.services.artifacts import get_latest_artifact_job
from eogum.services.database import get_db
from eogum.services.r2 import generate_presigned_download

router = APIRouter(prefix="/projects/{project_id}/download", tags=["downloads"])

_DOWNLOAD_TYPES = {
    "fcpxml",
    "srt",
    "report",
    "project_json",
    "storyline",
    "source",
    "preview",
    "sync_diagnostics",
    "llm_io_log",
}


def _project_access_query(db, current_user: CurrentUser, select: str = "*"):
    query = db.table("projects").select(select)
    if not current_user.is_admin:
        query = query.eq("user_id", current_user.id)
    return query


def _get_accessible_project(db, project_id: str, current_user: CurrentUser, select: str = "*") -> dict:
    project = _project_access_query(db, current_user, select).eq("id", project_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")
    return project.data


@router.get("/extra-source/{index}", response_model=DownloadResponse)
def download_extra_source(project_id: str, index: int, current_user: CurrentUser = Depends(get_current_user)):
    db = get_db()

    project_data = _get_accessible_project(db, project_id, current_user, "user_id, extra_sources")

    extras = project_data.get("extra_sources") or []
    if index < 0 or index >= len(extras):
        raise HTTPException(status_code=404, detail="멀티캠 소스를 찾을 수 없습니다")

    src = extras[index]
    filename = src["filename"]
    download_url = generate_presigned_download(src["r2_key"], filename)
    return DownloadResponse(download_url=download_url, filename=filename)


@router.get("/{file_type}", response_model=DownloadResponse)
def download_file(project_id: str, file_type: str, current_user: CurrentUser = Depends(get_current_user)):
    if file_type not in _DOWNLOAD_TYPES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 파일 타입: {file_type}")

    db = get_db()

    # Source download: use project's source_r2_key directly
    if file_type == "source":
        project_data = _get_accessible_project(
            db,
            project_id,
            current_user,
            "name, user_id, source_r2_key, source_filename",
        )

        r2_key = project_data.get("source_r2_key")
        if not r2_key:
            raise HTTPException(status_code=404, detail="원본 소스 파일을 찾을 수 없습니다")

        filename = project_data.get("source_filename") or f"{project_data['name']}.mp4"
        download_url = generate_presigned_download(r2_key, filename)
        return DownloadResponse(download_url=download_url, filename=filename)

    # Verify project access
    project_data = _get_accessible_project(db, project_id, current_user, "name, user_id")

    # Get job with results
    job = get_latest_artifact_job(db, project_id, user_id=project_data["user_id"], select="result_r2_keys")
    if not job:
        raise HTTPException(status_code=404, detail="결과 파일이 없습니다")

    r2_keys = job["result_r2_keys"]
    r2_key = r2_keys.get(file_type)
    if not r2_key:
        raise HTTPException(status_code=404, detail=f"{file_type} 파일을 찾을 수 없습니다")

    ext_map = {
        "fcpxml": ".fcpxml",
        "srt": ".srt",
        "report": ".md",
        "project_json": ".json",
        "storyline": ".json",
        "preview": ".mp4",
        "sync_diagnostics": ".sync_diagnostics.json",
        "llm_io_log": ".llm_io.jsonl",
    }
    filename = f"{project_data['name']}{ext_map.get(file_type, '')}"

    download_url = generate_presigned_download(r2_key, filename)
    return DownloadResponse(download_url=download_url, filename=filename)
