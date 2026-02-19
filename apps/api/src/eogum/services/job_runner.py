"""Sequential job runner for processing video projects."""

import logging
import threading
from collections import deque
from pathlib import Path

from eogum.config import settings
from eogum.services import avid, credit, email, r2
from eogum.services.database import get_db

logger = logging.getLogger(__name__)

_queue: deque[str] = deque()
_running = False
_lock = threading.Lock()


def enqueue(project_id: str) -> None:
    """Add project to processing queue."""
    _queue.append(project_id)
    _maybe_start_worker()


def _maybe_start_worker() -> None:
    global _running
    with _lock:
        if _running:
            return
        _running = True
    thread = threading.Thread(target=_worker_loop, daemon=True)
    thread.start()


def _worker_loop() -> None:
    global _running
    while _queue:
        project_id = _queue.popleft()
        try:
            _process_project(project_id)
        except Exception:
            logger.exception("Fatal error processing project %s", project_id)
    with _lock:
        _running = False


def _process_project(project_id: str) -> None:
    db = get_db()

    # Load project
    project = db.table("projects").select("*").eq("id", project_id).single().execute().data
    user_id = project["user_id"]
    duration = project["source_duration_seconds"]

    # Get user email
    user = db.table("profiles").select("id, display_name").eq("id", user_id).single().execute().data
    auth_user = db.auth.admin.get_user_by_id(user_id)
    user_email = auth_user.user.email

    # Update project status
    db.table("projects").update({"status": "processing"}).eq("id", project_id).execute()

    # Create job record
    job = db.table("jobs").insert({
        "project_id": project_id,
        "user_id": user_id,
        "type": project["cut_type"],
        "status": "running",
    }).execute().data[0]
    job_id = job["id"]

    # Ensure temp dirs
    temp_dir = settings.avid_temp_dir / project_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    output_dir = temp_dir / "output"
    output_dir.mkdir(exist_ok=True)

    try:
        # 1. Hold credits
        credit.hold_credits(user_id, duration, job_id)

        # 2. Download source from R2
        source_ext = Path(project["source_filename"]).suffix
        source_path = str(temp_dir / f"source{source_ext}")
        _update_progress(db, job_id, 5)

        r2.download_file(project["source_r2_key"], source_path)
        _update_progress(db, job_id, 10)

        # 3. Transcribe
        srt_path = avid.transcribe(source_path, language=project["language"], output_dir=str(temp_dir))
        _update_progress(db, job_id, 30)

        # 4. Transcript overview (Pass 1)
        storyline_path = avid.transcript_overview(srt_path, output_path=str(temp_dir / "storyline.json"))
        _update_progress(db, job_id, 50)

        # 5. Cut (Pass 2)
        cut_fn = avid.subtitle_cut if project["cut_type"] == "subtitle_cut" else avid.podcast_cut
        result_paths = cut_fn(
            source_path=source_path,
            srt_path=srt_path,
            context_path=storyline_path,
            output_dir=str(output_dir),
        )
        _update_progress(db, job_id, 75)

        # 5.5. Generate low-quality preview for review page
        import subprocess as sp
        preview_path = str(output_dir / "preview.mp4")
        try:
            sp.run([
                "ffmpeg", "-i", source_path,
                "-vf", "scale=-2:480",
                "-c:v", "libx264", "-preset", "fast", "-crf", "28",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                "-y", preview_path,
            ], check=True, timeout=600, capture_output=True)
            result_paths["preview"] = preview_path
        except Exception:
            logger.warning("Preview generation failed for project %s, skipping", project_id)

        # 6. Upload results to R2
        r2_keys = {}
        for key, local_path in result_paths.items():
            content_type = _guess_content_type(key)
            r2_key = f"results/{project_id}/{Path(local_path).name}"
            r2.upload_file(local_path, r2_key, content_type)
            r2_keys[key] = r2_key
        _update_progress(db, job_id, 85)

        # 7. Save edit report to DB
        if "report" in result_paths:
            report_text = Path(result_paths["report"]).read_text(encoding="utf-8")
            _save_report(db, project_id, duration, report_text)

        # 8. Confirm credit usage
        credit.confirm_usage(user_id, duration, job_id)

        # 9. Mark complete
        db.table("jobs").update({
            "status": "completed",
            "progress": 100,
            "result_r2_keys": r2_keys,
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        db.table("projects").update({"status": "completed"}).eq("id", project_id).execute()

        # 10. Send email
        report = db.table("edit_reports").select("cut_percentage").eq("project_id", project_id).single().execute()
        cut_pct = report.data["cut_percentage"] if report.data else 0
        email.send_completion_email(user_email, project["name"], project_id, cut_pct)

    except Exception as e:
        logger.exception("Project %s failed", project_id)

        # Release held credits
        try:
            credit.release_hold(user_id, duration, job_id)
        except Exception:
            logger.exception("Failed to release credit hold for project %s", project_id)

        # Mark failed
        db.table("jobs").update({
            "status": "failed",
            "error_message": str(e)[:1000],
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        db.table("projects").update({"status": "failed"}).eq("id", project_id).execute()

        try:
            email.send_failure_email(user_email, project["name"], project_id, str(e)[:200])
        except Exception:
            logger.exception("Failed to send failure email for project %s", project_id)

    finally:
        # Cleanup temp files
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


def _update_progress(db, job_id: str, progress: int) -> None:
    db.table("jobs").update({"progress": progress}).eq("id", job_id).execute()


def _save_report(db, project_id: str, total_duration: int, report_markdown: str) -> None:
    # Parse cut stats from report (simple heuristic)
    cut_duration = 0
    cut_percentage = 0.0

    for line in report_markdown.split("\n"):
        if "절약" in line or "saved" in line.lower() or "cut" in line.lower():
            # Try to extract percentage
            import re
            pct_match = re.search(r"(\d+\.?\d*)%", line)
            if pct_match:
                cut_percentage = float(pct_match.group(1))
                cut_duration = int(total_duration * cut_percentage / 100)
                break

    db.table("edit_reports").insert({
        "project_id": project_id,
        "total_duration_seconds": total_duration,
        "cut_duration_seconds": cut_duration,
        "cut_percentage": cut_percentage,
        "edit_summary": {},
        "report_markdown": report_markdown,
    }).execute()


def _guess_content_type(key: str) -> str:
    types = {
        "fcpxml": "application/xml",
        "srt": "text/plain",
        "report": "text/markdown",
        "project_json": "application/json",
        "preview": "video/mp4",
    }
    return types.get(key, "application/octet-stream")
