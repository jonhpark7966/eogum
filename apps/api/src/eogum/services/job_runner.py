"""Sequential job runner for processing video projects."""

import json
import logging
import threading
from collections import deque
from pathlib import Path

from eogum.config import settings
from eogum.services import avid, credit, email, r2
from eogum.services.database import get_db

logger = logging.getLogger(__name__)

_queue: deque[dict[str, str | None]] = deque()
_running = False
_lock = threading.Lock()


def enqueue(project_id: str) -> None:
    """Add project to processing queue."""
    _queue.append({"kind": "initial", "project_id": project_id, "job_id": None})
    _maybe_start_worker()


def enqueue_reprocess(project_id: str, job_id: str) -> None:
    """Add reprocess task to queue."""
    _queue.append({"kind": "reprocess", "project_id": project_id, "job_id": job_id})
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
        item = _queue.popleft()
        project_id = item["project_id"]
        try:
            if item["kind"] == "reprocess":
                _reprocess_project(project_id, item["job_id"])
            else:
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

        # 2.5. Download extra sources (multicam)
        extra_source_paths: list[str] = []
        for i, es in enumerate(project.get("extra_sources") or []):
            ext = Path(es["filename"]).suffix
            local_path = str(temp_dir / f"extra_{i}{ext}")
            r2.download_file(es["r2_key"], local_path)
            extra_source_paths.append(local_path)

        _update_progress(db, job_id, 10)

        # 3. Transcribe
        transcription_context = (project.get("settings") or {}).get("transcription_context")
        srt_path = avid.transcribe(source_path, language=project["language"], output_dir=str(temp_dir), context=transcription_context)
        _update_progress(db, job_id, 30)

        # 4. Transcript overview (Pass 1)
        storyline_path = avid.transcript_overview(srt_path, output_path=str(output_dir / "storyline.json"))
        _update_progress(db, job_id, 50)

        # 5. Cut (Pass 2)
        cut_fn = avid.subtitle_cut if project["cut_type"] == "subtitle_cut" else avid.podcast_cut
        result_paths = cut_fn(
            source_path=source_path,
            srt_path=srt_path,
            context_path=storyline_path,
            output_dir=str(output_dir),
            extra_sources=extra_source_paths or None,
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
        try:
            report = db.table("edit_reports").select("cut_percentage").eq("project_id", project_id).limit(1).execute()
            cut_pct = report.data[0]["cut_percentage"] if report.data else 0
            email.send_completion_email(user_email, project["name"], project_id, cut_pct)
        except Exception:
            logger.exception("Failed to send completion email for project %s", project_id)

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


def _resolve_extra_source_offsets(extra_sources: list[dict]) -> list[int] | None:
    if not extra_sources:
        return None

    offsets = [item.get("offset_ms") for item in extra_sources]
    if not any(offset is not None for offset in offsets):
        return None
    if not all(offset is not None for offset in offsets):
        raise ValueError("manual offset 을 사용할 때는 모든 extra source 에 offset_ms 를 지정해야 합니다")
    return [int(offset) for offset in offsets]


def _project_json_has_extra_sources(project_json_path: Path) -> bool:
    data = json.loads(project_json_path.read_text(encoding="utf-8"))
    return len(data.get("source_files") or []) > 1


def _plan_reprocess_steps(
    *,
    has_evaluation: bool,
    desired_extra_sources: bool,
    current_project_has_extra_sources: bool,
) -> list[str]:
    steps: list[str] = []
    if has_evaluation:
        steps.append("apply-evaluation")
    if desired_extra_sources:
        steps.append("rebuild-multicam")
    elif current_project_has_extra_sources:
        steps.append("clear-extra-sources")
    steps.append("export-project")
    return steps


def _reprocess_project(project_id: str, job_id: str | None) -> None:
    import shutil

    if not job_id:
        raise RuntimeError("reprocess job_id is required")

    db = get_db()
    project = db.table("projects").select("*").eq("id", project_id).single().execute().data
    user_id = project["user_id"]

    db.table("projects").update({"status": "processing"}).eq("id", project_id).execute()
    db.table("jobs").update({
        "status": "running",
        "progress": 5,
        "error_message": None,
    }).eq("id", job_id).execute()

    temp_dir = settings.avid_temp_dir / f"multicam_{project_id}"
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_dir = temp_dir / "output"
        output_dir.mkdir(exist_ok=True)

        completed_job = (
            db.table("jobs")
            .select("id, result_r2_keys")
            .eq("project_id", project_id)
            .eq("status", "completed")
            .order("created_at", desc=True)
            .limit(1)
            .maybe_single()
            .execute()
        )
        if not completed_job.data or not completed_job.data.get("result_r2_keys"):
            raise RuntimeError("완료된 작업이 없습니다. 전체 재처리가 필요합니다.")

        r2_keys = dict(completed_job.data["result_r2_keys"])
        project_json_key = r2_keys.get("project_json")
        if not project_json_key:
            raise RuntimeError("프로젝트 JSON이 없습니다. 전체 재처리가 필요합니다.")

        project_json_bytes = r2.download_to_bytes(project_json_key)
        local_project_json = temp_dir / "input.project.avid.json"
        local_project_json.write_bytes(project_json_bytes)
        working_project_json = local_project_json

        stored_project_json = json.loads(project_json_bytes.decode("utf-8"))
        current_project_has_extra_sources = len(stored_project_json.get("source_files") or []) > 1

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

        has_extra_sources = bool(project.get("extra_sources"))
        extra_offsets = _resolve_extra_source_offsets(project.get("extra_sources") or [])

        if not eval_segments and not has_extra_sources and not current_project_has_extra_sources:
            raise RuntimeError("평가 데이터 또는 적용할 extra source 변경이 필요합니다")

        evaluation_path = None
        if eval_segments:
            evaluation_path = temp_dir / "evaluation.json"
            serialized_evaluation = (
                evaluation_payload
                if isinstance(evaluation_payload, dict)
                else {"segments": eval_segments}
            )
            evaluation_path.write_text(
                json.dumps(serialized_evaluation, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        source_path = None
        extra_paths: list[str] = []
        if has_extra_sources:
            source_ext = Path(project["source_filename"]).suffix
            local_source_path = temp_dir / f"source{source_ext}"
            r2.download_file(project["source_r2_key"], str(local_source_path))
            source_path = str(local_source_path)

            for i, es in enumerate(project["extra_sources"]):
                ext = Path(es["filename"]).suffix
                local_path = temp_dir / f"extra_{i}{ext}"
                r2.download_file(es["r2_key"], str(local_path))
                extra_paths.append(str(local_path))

        steps = _plan_reprocess_steps(
            has_evaluation=bool(eval_segments),
            desired_extra_sources=has_extra_sources,
            current_project_has_extra_sources=_project_json_has_extra_sources(working_project_json),
        )

        db.table("jobs").update({"progress": 25}).eq("id", job_id).execute()

        if "apply-evaluation" in steps:
            eval_output = temp_dir / "01_eval_applied.project.avid.json"
            payload = avid.apply_evaluation(
                project_json_path=str(working_project_json),
                evaluation_path=str(evaluation_path),
                output_project_json=str(eval_output),
            )
            working_project_json = Path(payload["artifacts"]["project_json"])
            logger.info("Applied evaluation via avid-cli: %s", payload)

        if "rebuild-multicam" in steps:
            multicam_output = temp_dir / "02_multicam.project.avid.json"
            payload = avid.rebuild_multicam(
                project_json_path=str(working_project_json),
                source_path=str(source_path),
                extra_sources=extra_paths,
                output_project_json=str(multicam_output),
                offsets=extra_offsets,
            )
            working_project_json = Path(payload["artifacts"]["project_json"])
            logger.info("Rebuilt multicam via avid-cli: %s", payload)
        elif "clear-extra-sources" in steps:
            clear_output = temp_dir / "02_cleared.project.avid.json"
            payload = avid.clear_extra_sources(
                project_json_path=str(working_project_json),
                output_project_json=str(clear_output),
            )
            working_project_json = Path(payload["artifacts"]["project_json"])
            logger.info("Cleared extra sources via avid-cli: %s", payload)

        db.table("jobs").update({"progress": 70}).eq("id", job_id).execute()

        payload = avid.export_project(
            project_json_path=str(working_project_json),
            output_dir=str(output_dir),
            content_mode="cut" if eval_segments else "disabled",
        )
        artifacts = payload.get("artifacts") or {}
        updated_json = working_project_json
        fcpxml_path = Path(artifacts["fcpxml"])
        srt_path = Path(artifacts["srt"]) if artifacts.get("srt") else None

        new_r2_keys = dict(r2_keys)

        pj_r2_key = f"results/{project_id}/{updated_json.name}"
        r2.upload_file(str(updated_json), pj_r2_key, "application/json")
        new_r2_keys["project_json"] = pj_r2_key

        fcpxml_r2_key = f"results/{project_id}/{fcpxml_path.name}"
        r2.upload_file(str(fcpxml_path), fcpxml_r2_key, "application/xml")
        new_r2_keys["fcpxml"] = fcpxml_r2_key

        if srt_path:
            srt_r2_key = f"results/{project_id}/{srt_path.name}"
            r2.upload_file(str(srt_path), srt_r2_key, "text/plain")
            new_r2_keys["srt"] = srt_r2_key

        sync_diagnostics_path = artifacts.get("sync_diagnostics")
        if sync_diagnostics_path:
            sync_path = Path(sync_diagnostics_path)
            sync_r2_key = f"results/{project_id}/{sync_path.name}"
            r2.upload_file(str(sync_path), sync_r2_key, "application/json")
            new_r2_keys["sync_diagnostics"] = sync_r2_key
        elif "sync_diagnostics" in new_r2_keys:
            new_r2_keys.pop("sync_diagnostics", None)

        db.table("jobs").update({
            "status": "completed",
            "progress": 100,
            "result_r2_keys": new_r2_keys,
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        db.table("projects").update({"status": "completed"}).eq("id", project_id).execute()
        logger.info("Reprocess completed for project %s", project_id)
    except Exception as exc:
        logger.exception("Project reprocess failed for project %s", project_id)
        db.table("jobs").update({
            "status": "failed",
            "error_message": str(exc)[:1000],
            "completed_at": "now()",
        }).eq("id", job_id).execute()
        db.table("projects").update({"status": "reprocess_failed"}).eq("id", project_id).execute()
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _update_progress(db, job_id: str, progress: int) -> None:
    db.table("jobs").update({"progress": progress}).eq("id", job_id).execute()


def _save_report(db, project_id: str, total_duration: int, report_markdown: str) -> None:
    import re

    cut_duration = 0
    cut_percentage = 0.0

    # Try to find "합계" row in markdown table: | **합계** | **878개** | **24:31.665** |
    total_match = re.search(r"합계.*?\|\s*\**(\d+):(\d+)\.(\d+)\**\s*\|", report_markdown)
    if total_match:
        minutes = int(total_match.group(1))
        seconds = int(total_match.group(2))
        cut_duration = minutes * 60 + seconds
        if total_duration > 0:
            cut_percentage = round(cut_duration / total_duration * 100, 1)
    else:
        # Fallback: look for percentage pattern near "절약" or "saved"
        for line in report_markdown.split("\n"):
            if "절약" in line or "saved" in line.lower():
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
        "storyline": "application/json",
        "sync_diagnostics": "application/json",
        "preview": "video/mp4",
    }
    return types.get(key, "application/octet-stream")
