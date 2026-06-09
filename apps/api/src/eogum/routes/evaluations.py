"""Evaluation routes for segment review and feedback."""

from collections import Counter
import json
import logging
from pathlib import Path
import tempfile
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from eogum.auth import get_user_id
from eogum.config import settings
from eogum.models.schemas import (
    ConfusionMatrix,
    DisagreementDetail,
    EvalMetrics,
    EvalReportResponse,
    EvaluationResponse,
    EvaluationSave,
    FinalPreviewJobResponse,
    FinalPreviewRequest,
    ReasonBreakdown,
    SegmentsResponse,
    VideoUrlResponse,
)
from eogum.services import avid
from eogum.services.artifacts import get_latest_artifact_job
from eogum.services.database import get_db
from eogum.services.final_preview_cache import (
    decision_hash,
    preview_cache_paths,
    preview_cache_ready,
)
from eogum.services.r2 import download_to_bytes, generate_presigned_stream
from eogum.services.job_runner import enqueue_final_preview

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}", tags=["evaluations"])

STREAM_CHUNK_SIZE = 1024 * 1024


def _api_public_base(request: Request) -> str:
    if settings.api_public_url:
        return settings.api_public_url.rstrip("/")
    return f"{str(request.base_url).rstrip('/')}/api/v1"


def _local_preview_urls(request: Request, project_id: str, job_id: str, token: str) -> tuple[str, str]:
    base = _api_public_base(request)
    quoted_token = quote(token, safe="")
    path = f"{base}/projects/{project_id}/final-preview/{job_id}"
    return f"{path}/video?token={quoted_token}", f"{path}/captions?token={quoted_token}"


def _response_from_final_preview_job(job: dict, request: Request, project_id: str) -> FinalPreviewJobResponse:
    result_keys = job.get("result_r2_keys") or {}
    video_url = None
    captions_url = None

    cache_token = result_keys.get("cache_token")
    hash_value = result_keys.get("decision_hash")
    if cache_token and hash_value and preview_cache_ready(project_id, hash_value):
        video_url, captions_url = _local_preview_urls(request, project_id, job["id"], cache_token)
    else:
        preview_key = result_keys.get("final_preview")
        if preview_key:
            video_url = generate_presigned_stream(preview_key)

    return FinalPreviewJobResponse(
        job_id=job["id"],
        status=job["status"],
        progress=job.get("progress") or 0,
        error_message=job.get("error_message"),
        video_url=video_url,
        captions_url=captions_url,
        duration_ms=result_keys.get("duration_ms"),
    )


def _find_completed_cached_preview_job(db, project_id: str, user_id: str, hash_value: str) -> dict | None:
    result = (
        db.table("jobs")
        .select("id,status,progress,error_message,result_r2_keys,created_at")
        .eq("project_id", project_id)
        .eq("user_id", user_id)
        .eq("type", "final_preview")
        .eq("status", "completed")
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    for job in result.data or []:
        result_keys = job.get("result_r2_keys") or {}
        if result_keys.get("decision_hash") == hash_value and preview_cache_ready(project_id, hash_value):
            return job
    return None


def _verify_cached_preview_job(project_id: str, job_id: str, token: str) -> tuple[Path, Path]:
    db = get_db()
    job = (
        db.table("jobs")
        .select("id,project_id,type,status,result_r2_keys")
        .eq("id", job_id)
        .eq("project_id", project_id)
        .eq("type", "final_preview")
        .eq("status", "completed")
        .maybe_single()
        .execute()
    )
    if not job.data:
        raise HTTPException(status_code=404, detail="미리보기 작업을 찾을 수 없습니다")

    result_keys = job.data.get("result_r2_keys") or {}
    expected_token = result_keys.get("cache_token")
    hash_value = result_keys.get("decision_hash")
    if not expected_token or not hash_value or token != expected_token:
        raise HTTPException(status_code=403, detail="미리보기 접근 토큰이 유효하지 않습니다")

    video_path, captions_path = preview_cache_paths(project_id, hash_value)
    if not video_path.is_file() or not captions_path.is_file():
        raise HTTPException(status_code=404, detail="미리보기 캐시 파일을 찾을 수 없습니다")
    return video_path, captions_path


def _iter_file_range(path: Path, start: int, end: int):
    with path.open("rb") as file:
        file.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = file.read(min(STREAM_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _stream_cached_file(request: Request, path: Path, media_type: str) -> StreamingResponse:
    size = path.stat().st_size
    etag = f'"{path.stat().st_mtime_ns}-{size}"'
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=31536000, immutable",
        "ETag": etag,
    }

    range_header = request.headers.get("range")
    if range_header and range_header.startswith("bytes="):
        range_value = range_header.removeprefix("bytes=").split(",", 1)[0].strip()
        start_raw, _, end_raw = range_value.partition("-")
        try:
            if start_raw:
                start = int(start_raw)
                end = int(end_raw) if end_raw else size - 1
            else:
                suffix_length = int(end_raw)
                start = max(0, size - suffix_length)
                end = size - 1
        except ValueError:
            raise HTTPException(status_code=416, detail="Requested Range Not Satisfiable") from None
        if start >= size or end < start:
            raise HTTPException(status_code=416, detail="Requested Range Not Satisfiable")
        end = min(end, size - 1)
        headers.update({
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        })
        return StreamingResponse(
            _iter_file_range(path, start, end),
            status_code=206,
            media_type=media_type,
            headers=headers,
        )

    headers["Content-Length"] = str(size)
    return StreamingResponse(
        _iter_file_range(path, 0, size - 1),
        media_type=media_type,
        headers=headers,
    )


def _normalize_evaluation_payload(segments_value) -> dict:
    if isinstance(segments_value, dict):
        payload = dict(segments_value)
        payload["segments"] = payload.get("segments") or []
        return payload
    return {"segments": segments_value or []}


def _evaluation_response_from_row(row: dict) -> EvaluationResponse:
    payload = _normalize_evaluation_payload(row.get("segments"))
    extra_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "review_scope", "join_strategy", "segments"}
    }
    return EvaluationResponse(
        id=row["id"],
        project_id=row["project_id"],
        evaluator_id=row["evaluator_id"],
        version=row["version"],
        avid_version=row.get("avid_version"),
        eogum_version=row.get("eogum_version"),
        schema_version=payload.get("schema_version"),
        review_scope=payload.get("review_scope"),
        join_strategy=payload.get("join_strategy"),
        segments=payload.get("segments") or [],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        **extra_payload,
    )


def _get_completed_job(db, project_id: str, user_id: str):
    """Get the latest completed job for a project, verifying ownership."""
    project = (
        db.table("projects")
        .select("id, user_id")
        .eq("id", project_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    job = get_latest_artifact_job(db, project_id, select="result_r2_keys")
    if not job:
        raise HTTPException(status_code=404, detail="완료된 작업이 없습니다")

    return job["result_r2_keys"]


@router.get("/segments", response_model=SegmentsResponse)
def get_segments(project_id: str, user_id: str = Depends(get_user_id)):
    """Get engine-native review segments from avid-cli."""
    db = get_db()
    r2_keys = _get_completed_job(db, project_id, user_id)

    project_json_key = r2_keys.get("project_json")
    if not project_json_key:
        raise HTTPException(status_code=404, detail="프로젝트 JSON을 찾을 수 없습니다")

    raw = download_to_bytes(project_json_key)
    avid_data = json.loads(raw)

    transcription = avid_data.get("transcription")
    if not transcription or not transcription.get("segments"):
        raise HTTPException(status_code=404, detail="자막 데이터가 없습니다")

    source_duration_ms = 0
    for sf in avid_data.get("source_files", []):
        info = sf.get("info", {})
        if info.get("duration_ms"):
            source_duration_ms = max(source_duration_ms, info["duration_ms"])

    settings.avid_temp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"review_segments_{project_id}_", dir=str(settings.avid_temp_dir)) as temp_dir:
        local_project_json = Path(temp_dir) / "input.project.avid.json"
        local_project_json.write_bytes(raw)
        payload = avid.review_segments(str(local_project_json))

    payload["source_duration_ms"] = source_duration_ms
    return payload


@router.get("/video-url", response_model=VideoUrlResponse)
def get_video_url(project_id: str, user_id: str = Depends(get_user_id)):
    """Get presigned streaming URL for the preview video."""
    db = get_db()
    r2_keys = _get_completed_job(db, project_id, user_id)

    preview_key = r2_keys.get("preview")

    # Get project info (duration + source fallback)
    project = (
        db.table("projects")
        .select("source_duration_seconds, source_r2_key")
        .eq("id", project_id)
        .single()
        .execute()
    )
    duration_ms = (project.data.get("source_duration_seconds") or 0) * 1000

    # Fall back to source video if no preview exists
    stream_key = preview_key or project.data.get("source_r2_key")
    if not stream_key:
        raise HTTPException(status_code=404, detail="프리뷰 영상이 없습니다")

    video_url = generate_presigned_stream(stream_key)
    return VideoUrlResponse(video_url=video_url, duration_ms=duration_ms)


@router.get("/evaluation", response_model=EvaluationResponse)
def get_evaluation(project_id: str, user_id: str = Depends(get_user_id)):
    """Get existing evaluation for this project by the current user."""
    db = get_db()

    # Verify project ownership
    project = (
        db.table("projects")
        .select("id, user_id")
        .eq("id", project_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    result = (
        db.table("evaluations")
        .select("*")
        .eq("project_id", project_id)
        .eq("evaluator_id", user_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="평가 데이터가 없습니다")

    return _evaluation_response_from_row(result.data[0])


def _save_evaluation_payload(db, project_id: str, user_id: str, payload: dict) -> EvaluationResponse:
    avid_version = avid.get_version()
    eogum_version = None

    try:
        import subprocess

        eogum_repo = Path(__file__).resolve().parents[5]
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(eogum_repo),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            eogum_version = result.stdout.strip()
    except Exception:
        pass

    result = (
        db.table("evaluations")
        .upsert(
            {
                "project_id": project_id,
                "evaluator_id": user_id,
                "segments": payload,
                "avid_version": avid_version,
                "eogum_version": eogum_version,
            },
            on_conflict="project_id,evaluator_id",
        )
        .execute()
    )
    return _evaluation_response_from_row(result.data[0])


@router.post("/evaluation", response_model=EvaluationResponse)
def save_evaluation(
    project_id: str,
    req: EvaluationSave,
    user_id: str = Depends(get_user_id),
):
    """Save or update evaluation (upsert on project_id + evaluator_id)."""
    db = get_db()

    # Verify project ownership
    project = (
        db.table("projects")
        .select("id, user_id")
        .eq("id", project_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    return _save_evaluation_payload(db, project_id, user_id, req.model_dump())


@router.post("/final-preview", response_model=FinalPreviewJobResponse)
def start_final_preview(
    project_id: str,
    req: FinalPreviewRequest,
    request: Request,
    user_id: str = Depends(get_user_id),
):
    db = get_db()

    project = (
        db.table("projects")
        .select("id, user_id, source_duration_seconds")
        .eq("id", project_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")

    payload = req.model_dump()
    _save_evaluation_payload(db, project_id, user_id, payload)
    hash_value = decision_hash(payload)

    cached_job = _find_completed_cached_preview_job(db, project_id, user_id, hash_value)
    if cached_job:
        return _response_from_final_preview_job(cached_job, request, project_id)

    job = (
        db.table("jobs")
        .insert({
            "project_id": project_id,
            "user_id": user_id,
            "type": "final_preview",
            "status": "pending",
            "progress": 0,
            "input_payload": payload,
            "result_r2_keys": {
                "decision_hash": hash_value,
            },
        })
        .execute()
        .data[0]
    )
    enqueue_final_preview(project_id, job["id"])
    return FinalPreviewJobResponse(
        job_id=job["id"],
        status=job["status"],
        progress=job["progress"],
        duration_ms=(project.data.get("source_duration_seconds") or 0) * 1000,
    )


@router.get("/final-preview/{job_id}", response_model=FinalPreviewJobResponse)
def get_final_preview(project_id: str, job_id: str, request: Request, user_id: str = Depends(get_user_id)):
    db = get_db()

    job = (
        db.table("jobs")
        .select("*")
        .eq("id", job_id)
        .eq("project_id", project_id)
        .eq("user_id", user_id)
        .eq("type", "final_preview")
        .maybe_single()
        .execute()
    )
    if not job.data:
        raise HTTPException(status_code=404, detail="미리보기 작업을 찾을 수 없습니다")

    return _response_from_final_preview_job(job.data, request, project_id)


@router.get("/final-preview/{job_id}/video")
def stream_final_preview_video(
    project_id: str,
    job_id: str,
    request: Request,
    token: str = Query(...),
):
    video_path, _ = _verify_cached_preview_job(project_id, job_id, token)
    return _stream_cached_file(request, video_path, "video/mp4")


@router.get("/final-preview/{job_id}/captions")
def stream_final_preview_captions(
    project_id: str,
    job_id: str,
    request: Request,
    token: str = Query(...),
):
    _, captions_path = _verify_cached_preview_job(project_id, job_id, token)
    return _stream_cached_file(request, captions_path, "text/vtt; charset=utf-8")


@router.get("/eval-report", response_model=EvalReportResponse)
def get_eval_report(project_id: str, user_id: str = Depends(get_user_id)):
    """Compare AI decisions vs human ground truth and produce a report."""
    db = get_db()

    # Get evaluation
    eval_result = (
        db.table("evaluations")
        .select("*")
        .eq("project_id", project_id)
        .eq("evaluator_id", user_id)
        .limit(1)
        .execute()
    )
    if not eval_result.data:
        raise HTTPException(status_code=404, detail="평가 데이터가 없습니다")

    evaluation = eval_result.data[0]
    payload = _normalize_evaluation_payload(evaluation["segments"])
    segments = payload.get("segments") or []

    # Classify each segment
    tp = tn = fp = fn = 0
    ai_cut_ms = 0
    truth_cut_ms = 0
    ai_cut_count = 0
    truth_cut_count = 0
    fp_reasons: Counter = Counter()
    fn_reasons: Counter = Counter()
    fp_ms: Counter = Counter()
    fn_ms: Counter = Counter()
    disagreements: list[DisagreementDetail] = []

    for seg in segments:
        ai = seg.get("ai") or {}
        human = seg.get("human")
        ai_action = ai.get("action", "keep")
        ai_reason = ai.get("reason", "")
        duration_ms = seg.get("end_ms", 0) - seg.get("start_ms", 0)

        # Ground truth: human if reviewed, else same as AI (implicit agree)
        if human:
            truth_action = human.get("action", "keep")
            human_reason = human.get("reason", "")
            human_note = human.get("note", "")
        else:
            truth_action = ai_action
            human_reason = ""
            human_note = ""

        # Count AI cuts and truth cuts
        if ai_action == "cut":
            ai_cut_count += 1
            ai_cut_ms += duration_ms
        if truth_action == "cut":
            truth_cut_count += 1
            truth_cut_ms += duration_ms

        # Confusion matrix (cut = positive)
        if ai_action == "cut" and truth_action == "cut":
            tp += 1
        elif ai_action == "keep" and truth_action == "keep":
            tn += 1
        elif ai_action == "cut" and truth_action == "keep":
            fp += 1
            fp_reasons[ai_reason or "(없음)"] += 1
            fp_ms[ai_reason or "(없음)"] += duration_ms
            disagreements.append(DisagreementDetail(
                index=seg["index"],
                start_ms=seg["start_ms"],
                end_ms=seg["end_ms"],
                text=seg.get("text", ""),
                ai_action=ai_action,
                ai_reason=ai_reason,
                human_action=truth_action,
                human_reason=human_reason,
                human_note=human_note,
            ))
        elif ai_action == "keep" and truth_action == "cut":
            fn += 1
            fn_reasons[human_reason or "(없음)"] += 1
            fn_ms[human_reason or "(없음)"] += duration_ms
            disagreements.append(DisagreementDetail(
                index=seg["index"],
                start_ms=seg["start_ms"],
                end_ms=seg["end_ms"],
                text=seg.get("text", ""),
                ai_action=ai_action,
                ai_reason=ai_reason,
                human_action=truth_action,
                human_reason=human_reason,
                human_note=human_note,
            ))

    total = tp + tn + fp + fn
    total_disagree = fp + fn
    agreement_rate = (total - total_disagree) / total if total > 0 else 0.0

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    accuracy = (tp + tn) / total if total > 0 else 0.0

    human_reviewed = sum(1 for s in segments if s.get("human"))

    return EvalReportResponse(
        project_id=project_id,
        avid_version=evaluation.get("avid_version"),
        eogum_version=evaluation.get("eogum_version"),
        total_segments=total,
        human_reviewed=human_reviewed,
        implicit_agree=total - human_reviewed,
        agreement_rate=round(agreement_rate, 4),
        confusion=ConfusionMatrix(tp=tp, tn=tn, fp=fp, fn=fn),
        metrics=EvalMetrics(
            accuracy=round(accuracy, 4),
            precision=round(precision, 4),
            recall=round(recall, 4),
            f1=round(f1, 4),
        ),
        ai_cut_count=ai_cut_count,
        ai_cut_ms=ai_cut_ms,
        truth_cut_count=truth_cut_count,
        truth_cut_ms=truth_cut_ms,
        fp_reasons=sorted(
            [ReasonBreakdown(reason=r, count=c, total_ms=fp_ms[r]) for r, c in fp_reasons.items()],
            key=lambda x: x.count, reverse=True,
        ),
        fn_reasons=sorted(
            [ReasonBreakdown(reason=r, count=c, total_ms=fn_ms[r]) for r, c in fn_reasons.items()],
            key=lambda x: x.count, reverse=True,
        ),
        disagreements=sorted(disagreements, key=lambda x: x.index),
    )
