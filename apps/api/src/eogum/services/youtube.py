"""YouTube download service using yt-dlp."""

import json
import logging
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from eogum.config import settings
from eogum.services import r2

logger = logging.getLogger(__name__)

# In-memory download task tracking
_tasks: dict[str, "DownloadTask"] = {}
_lock = threading.Lock()


@dataclass
class DownloadTask:
    id: str
    url: str
    user_id: str
    status: str = "pending"  # pending | downloading | uploading | completed | failed
    progress: float = 0.0  # 0-100
    error: str | None = None
    # Metadata (filled after info fetch)
    title: str = ""
    duration_seconds: int = 0
    filesize_bytes: int = 0
    filename: str = ""
    # Result (filled after completion)
    r2_key: str = ""
    local_path: str = ""


def get_video_info(url: str) -> dict:
    """Fetch YouTube video metadata without downloading."""
    result = subprocess.run(
        [
            "yt-dlp",
            "--dump-json",
            "--no-download",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"영상 정보를 가져올 수 없습니다: {result.stderr.strip()[:200]}")

    info = json.loads(result.stdout)
    return {
        "title": info.get("title", ""),
        "duration_seconds": int(info.get("duration", 0)),
        "filesize_approx_bytes": info.get("filesize_approx") or info.get("filesize") or 0,
        "thumbnail": info.get("thumbnail", ""),
        "uploader": info.get("uploader", ""),
        "upload_date": info.get("upload_date", ""),
    }


def start_download(url: str, user_id: str, info: dict) -> str:
    """Start background download and return task_id."""
    task_id = str(uuid.uuid4())
    task = DownloadTask(
        id=task_id,
        url=url,
        user_id=user_id,
        title=info.get("title", ""),
        duration_seconds=info.get("duration_seconds", 0),
        filesize_bytes=info.get("filesize_approx_bytes", 0),
    )

    with _lock:
        _tasks[task_id] = task

    thread = threading.Thread(target=_download_worker, args=(task,), daemon=True)
    thread.start()
    return task_id


def get_task(task_id: str) -> DownloadTask | None:
    return _tasks.get(task_id)


def remove_task(task_id: str) -> None:
    with _lock:
        _tasks.pop(task_id, None)


def _download_worker(task: DownloadTask) -> None:
    """Download video with yt-dlp, then upload to R2."""
    temp_dir = settings.avid_temp_dir / f"yt_{task.id}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        task.status = "downloading"
        output_template = str(temp_dir / "%(title).80s.%(ext)s")

        # Download with progress
        proc = subprocess.Popen(
            [
                "yt-dlp",
                "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
                "--merge-output-format", "mp4",
                "--newline",  # Progress on new lines for parsing
                "-o", output_template,
                task.url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            # Parse progress: [download]  45.2% of ~1.23GiB ...
            if "[download]" in line and "%" in line:
                try:
                    pct_str = line.split("%")[0].split()[-1]
                    pct = float(pct_str)
                    task.progress = pct * 0.8  # Download = 0-80%
                except (ValueError, IndexError):
                    pass

        proc.wait(timeout=7200)
        if proc.returncode != 0:
            raise RuntimeError("yt-dlp 다운로드 실패")

        # Find downloaded file
        downloaded = list(temp_dir.glob("*.*"))
        if not downloaded:
            raise RuntimeError("다운로드된 파일을 찾을 수 없습니다")

        local_path = str(downloaded[0])
        task.local_path = local_path
        task.filename = downloaded[0].name
        task.filesize_bytes = downloaded[0].stat().st_size

        # Get actual duration via ffprobe if not already known
        if task.duration_seconds == 0:
            task.duration_seconds = _get_duration(local_path)

        # Upload to R2
        task.status = "uploading"
        task.progress = 80

        ext = downloaded[0].suffix or ".mp4"
        r2_key = f"sources/{uuid.uuid4()}{ext}"

        r2.upload_file(local_path, r2_key, "video/mp4")

        task.r2_key = r2_key
        task.status = "completed"
        task.progress = 100

        logger.info("YouTube download completed: %s -> %s", task.url, r2_key)

    except Exception as e:
        logger.exception("YouTube download failed for task %s", task.id)
        task.status = "failed"
        task.error = str(e)[:500]

    finally:
        # Cleanup local files
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


def _get_duration(path: str) -> int:
    """Get video duration in seconds via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return int(float(result.stdout.strip()))
    except Exception:
        return 0
