"""YouTube URL download endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status

from eogum.auth import get_user_id
from eogum.models.schemas import (
    YouTubeDownloadRequest,
    YouTubeDownloadResponse,
    YouTubeInfoRequest,
    YouTubeInfoResponse,
    YouTubeTaskResponse,
)
from eogum.services import youtube
from eogum.services.credit import get_balance

router = APIRouter(prefix="/youtube", tags=["youtube"])


@router.post("/info", response_model=YouTubeInfoResponse)
def get_youtube_info(req: YouTubeInfoRequest, user_id: str = Depends(get_user_id)):
    """Fetch YouTube video metadata without downloading."""
    try:
        info = youtube.get_video_info(req.url)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="YouTube 영상 정보를 가져오는 중 오류가 발생했습니다",
        )
    return info


@router.post("/download", response_model=YouTubeDownloadResponse)
def start_youtube_download(
    req: YouTubeDownloadRequest,
    user_id: str = Depends(get_user_id),
):
    """Start background YouTube download + R2 upload."""
    # First fetch info to validate URL and get duration
    try:
        info = youtube.get_video_info(req.url)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Check credits before starting download
    duration = info["duration_seconds"]
    if duration > 0:
        balance = get_balance(user_id)
        if balance["available_seconds"] < duration:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"크레딧이 부족합니다. 필요: {duration}초, 사용 가능: {balance['available_seconds']}초",
            )

    task_id = youtube.start_download(req.url, user_id, info)
    return {
        "task_id": task_id,
        "title": info["title"],
        "duration_seconds": duration,
        "filesize_approx_bytes": info.get("filesize_approx_bytes", 0),
    }


@router.get("/download/{task_id}", response_model=YouTubeTaskResponse)
def get_download_status(task_id: str, user_id: str = Depends(get_user_id)):
    """Poll YouTube download progress."""
    task = youtube.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="다운로드 작업을 찾을 수 없습니다")
    if task.user_id != user_id:
        raise HTTPException(status_code=404, detail="다운로드 작업을 찾을 수 없습니다")

    return {
        "task_id": task.id,
        "status": task.status,
        "progress": round(task.progress, 1),
        "error": task.error,
        "r2_key": task.r2_key if task.status == "completed" else None,
        "filename": task.filename or None,
        "duration_seconds": task.duration_seconds,
        "filesize_bytes": task.filesize_bytes,
    }
