from fastapi import APIRouter, Depends, HTTPException, status

from eogum.auth import get_user_id
from eogum.models.schemas import (
    ProjectCreate,
    ProjectDetailResponse,
    ProjectResponse,
    UpdateExtraSourcesRequest,
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

    if project.data["status"] not in ("completed", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="완료 또는 실패한 프로젝트만 추가 소스를 설정할 수 있습니다",
        )

    extra_sources = [s.model_dump() for s in req.extra_sources]
    updated = db.table("projects").update({"extra_sources": extra_sources}).eq("id", project_id).execute().data[0]
    return updated


@router.post("/{project_id}/multicam", response_model=ProjectResponse)
def multicam_reprocess(project_id: str, user_id: str = Depends(get_user_id)):
    """Re-export FCPXML with evaluation overrides and optional multicam sources."""
    import json
    import logging
    import shutil
    import sys
    import threading
    from pathlib import Path

    from eogum.config import settings
    from eogum.services import avid, r2

    logger = logging.getLogger(__name__)
    db = get_db()

    project = db.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    if project.data["status"] not in ("completed", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="완료 또는 실패한 프로젝트만 재처리할 수 있습니다",
        )

    # Get existing completed job with result_r2_keys
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
    if not job or not job.data or not job.data.get("result_r2_keys"):
        raise HTTPException(status_code=404, detail="완료된 작업이 없습니다. 전체 재처리가 필요합니다.")

    r2_keys = job.data["result_r2_keys"]
    project_json_key = r2_keys.get("project_json")
    if not project_json_key:
        raise HTTPException(status_code=404, detail="프로젝트 JSON이 없습니다. 전체 재처리가 필요합니다.")

    # Fetch evaluation data (if exists)
    eval_result = (
        db.table("evaluations")
        .select("segments")
        .eq("project_id", project_id)
        .eq("evaluator_id", user_id)
        .limit(1)
        .execute()
    )
    eval_segments = eval_result.data[0]["segments"] if eval_result.data else None

    has_extra_sources = bool(project.data.get("extra_sources"))

    if not eval_segments and not has_extra_sources:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="평가 데이터 또는 추가 소스가 필요합니다",
        )

    # Mark processing
    db.table("projects").update({"status": "processing"}).eq("id", project_id).execute()

    def _reexport_worker():
        temp_dir = settings.avid_temp_dir / f"multicam_{project_id}"
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            output_dir = temp_dir / "output"
            output_dir.mkdir(exist_ok=True)

            # Download avid project JSON
            project_json_bytes = r2.download_to_bytes(project_json_key)
            local_project_json = temp_dir / Path(project_json_key).name
            local_project_json.write_bytes(project_json_bytes)

            # Add avid src to path for imports
            avid_src = str(settings.avid_cli_path / "src")
            if avid_src not in sys.path:
                sys.path.insert(0, avid_src)

            from avid.export.fcpxml import FCPXMLExporter
            from avid.models.project import Project

            # Load avid project
            avid_project = Project.load(local_project_json)
            logger.info(
                "Loaded avid project: %d sources, %d tracks, %d edit_decisions",
                len(avid_project.source_files), len(avid_project.tracks), len(avid_project.edit_decisions),
            )

            # Step 1: Apply evaluation overrides to edit_decisions
            if eval_segments:
                avid.apply_evaluation_to_project(avid_project, eval_segments)

            # Step 2: Add multicam sources (if any)
            # First, remove any previously-added extra sources to avoid duplicates
            if len(avid_project.source_files) > 1:
                primary_source_id = avid_project.source_files[0].id
                avid_project.source_files = [avid_project.source_files[0]]
                avid_project.tracks = [
                    t for t in avid_project.tracks
                    if t.source_file_id == primary_source_id
                ]
                logger.info("Stripped extra sources, now %d sources", len(avid_project.source_files))

            if has_extra_sources:
                from avid.services.audio_sync import AudioSyncService

                # Download main source
                source_ext = Path(project.data["source_filename"]).suffix
                source_path = temp_dir / f"source{source_ext}"
                r2.download_file(project.data["source_r2_key"], str(source_path))

                # Download extra sources
                extra_paths = []
                for i, es in enumerate(project.data["extra_sources"]):
                    ext = Path(es["filename"]).suffix
                    local_path = temp_dir / f"extra_{i}{ext}"
                    r2.download_file(es["r2_key"], str(local_path))
                    extra_paths.append(local_path)

                # Audio sync and add to project
                import asyncio

                sync_service = AudioSyncService()
                asyncio.run(sync_service.add_extra_sources(
                    avid_project, source_path, extra_paths,
                ))
                logger.info(
                    "Added %d extra sources. Now %d sources, %d tracks",
                    len(extra_paths), len(avid_project.source_files), len(avid_project.tracks),
                )

            # Step 3: Save updated project JSON
            updated_json = output_dir / local_project_json.name
            avid_project.save(updated_json)

            # Step 4: Export FCPXML
            exporter = FCPXMLExporter()
            stem = Path(project.data["source_filename"]).stem
            fcpxml_path = output_dir / f"{stem}_subtitle_cut.fcpxml"
            import asyncio
            # Evaluation applied → export as final cut (no disabled clips)
            asyncio.run(exporter.export(
                avid_project, fcpxml_path,
                silence_mode="cut",
                content_mode="cut" if eval_segments else "disabled",
            ))
            logger.info("Exported FCPXML: %s", fcpxml_path)

            # Step 5: Upload updated results to R2
            new_r2_keys = dict(r2_keys)

            # Upload project JSON
            pj_r2_key = f"results/{project_id}/{updated_json.name}"
            r2.upload_file(str(updated_json), pj_r2_key, "application/json")
            new_r2_keys["project_json"] = pj_r2_key

            # Upload FCPXML
            fcpxml_r2_key = f"results/{project_id}/{fcpxml_path.name}"
            r2.upload_file(str(fcpxml_path), fcpxml_r2_key, "application/xml")
            new_r2_keys["fcpxml"] = fcpxml_r2_key

            # Update job with new r2_keys
            db.table("jobs").update({
                "result_r2_keys": new_r2_keys,
            }).eq("id", job.data["id"]).execute()

            db.table("projects").update({"status": "completed"}).eq("id", project_id).execute()
            logger.info("Re-export completed for project %s", project_id)

        except Exception:
            logger.exception("Re-export failed for project %s", project_id)
            # Restore to completed — original results still valid
            db.table("projects").update({"status": "completed"}).eq("id", project_id).execute()

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    thread = threading.Thread(target=_reexport_worker, daemon=True)
    thread.start()

    return db.table("projects").select("*").eq("id", project_id).single().execute().data


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, user_id: str = Depends(get_user_id)):
    db = get_db()

    project = db.table("projects").select("id").eq("id", project_id).eq("user_id", user_id).single().execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    db.table("projects").delete().eq("id", project_id).execute()
