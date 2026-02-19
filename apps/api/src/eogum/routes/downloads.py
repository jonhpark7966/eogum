from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from eogum.auth import get_user_id
from eogum.models.schemas import DownloadResponse
from eogum.services.database import get_db
from eogum.services.r2 import generate_presigned_download

router = APIRouter(prefix="/projects/{project_id}/download", tags=["downloads"])

_DOWNLOAD_TYPES = {"fcpxml", "srt", "report", "project_json", "storyline", "source"}


@router.get("/extra-source/{index}", response_model=DownloadResponse)
def download_extra_source(project_id: str, index: int, user_id: str = Depends(get_user_id)):
    db = get_db()

    project = (
        db.table("projects")
        .select("user_id, extra_sources")
        .eq("id", project_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    extras = project.data.get("extra_sources") or []
    if index < 0 or index >= len(extras):
        raise HTTPException(status_code=404, detail="멀티캠 소스를 찾을 수 없습니다")

    src = extras[index]
    filename = src["filename"]
    download_url = generate_presigned_download(src["r2_key"], filename)
    return DownloadResponse(download_url=download_url, filename=filename)


@router.get("/{file_type}", response_model=DownloadResponse)
def download_file(project_id: str, file_type: str, user_id: str = Depends(get_user_id)):
    if file_type not in _DOWNLOAD_TYPES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 파일 타입: {file_type}")

    db = get_db()

    # Source download: use project's source_r2_key directly
    if file_type == "source":
        project = (
            db.table("projects")
            .select("name, user_id, source_r2_key, source_filename")
            .eq("id", project_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        if not project.data:
            raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

        r2_key = project.data.get("source_r2_key")
        if not r2_key:
            raise HTTPException(status_code=404, detail="원본 소스 파일을 찾을 수 없습니다")

        filename = project.data.get("source_filename") or f"{project.data['name']}.mp4"
        download_url = generate_presigned_download(r2_key, filename)
        return DownloadResponse(download_url=download_url, filename=filename)

    # Verify ownership
    project = db.table("projects").select("name, user_id").eq("id", project_id).eq("user_id", user_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    # Get job with results
    job = (
        db.table("jobs")
        .select("result_r2_keys")
        .eq("project_id", project_id)
        .eq("status", "completed")
        .order("created_at", desc=True)
        .limit(1)
        .single()
        .execute()
    )
    if not job.data or not job.data.get("result_r2_keys"):
        raise HTTPException(status_code=404, detail="결과 파일이 없습니다")

    r2_keys = job.data["result_r2_keys"]
    r2_key = r2_keys.get(file_type)
    if not r2_key:
        raise HTTPException(status_code=404, detail=f"{file_type} 파일을 찾을 수 없습니다")

    ext_map = {"fcpxml": ".fcpxml", "srt": ".srt", "report": ".md", "project_json": ".json", "storyline": ".json"}
    filename = f"{project.data['name']}{ext_map.get(file_type, '')}"

    download_url = generate_presigned_download(r2_key, filename)
    return DownloadResponse(download_url=download_url, filename=filename)
