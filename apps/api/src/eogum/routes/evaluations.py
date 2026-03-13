"""Evaluation routes for segment review and feedback."""

from collections import Counter
import json
import logging
from pathlib import Path
import tempfile

from fastapi import APIRouter, Depends, HTTPException

from eogum.auth import get_user_id
from eogum.config import settings
from eogum.models.schemas import (
    AiDecision,
    ConfusionMatrix,
    DisagreementDetail,
    EvalMetrics,
    EvalReportResponse,
    EvaluationResponse,
    EvaluationSave,
    ReasonBreakdown,
    SegmentsResponse,
    SegmentWithDecision,
    VideoUrlResponse,
)
from eogum.services import avid
from eogum.services.database import get_db
from eogum.services.r2 import download_to_bytes, generate_presigned_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}", tags=["evaluations"])


def _evaluation_metadata_from_segments(segments_value) -> tuple[str | None, str | None, str | None, list]:
    if isinstance(segments_value, dict):
        return (
            segments_value.get("schema_version"),
            segments_value.get("review_scope"),
            segments_value.get("join_strategy"),
            segments_value.get("segments") or [],
        )
    return None, None, None, segments_value or []


def _evaluation_response_from_row(row: dict) -> EvaluationResponse:
    schema_version, review_scope, join_strategy, segments = _evaluation_metadata_from_segments(
        row.get("segments")
    )
    return EvaluationResponse(
        id=row["id"],
        project_id=row["project_id"],
        evaluator_id=row["evaluator_id"],
        version=row["version"],
        avid_version=row.get("avid_version"),
        eogum_version=row.get("eogum_version"),
        schema_version=schema_version,
        review_scope=review_scope,
        join_strategy=join_strategy,
        segments=segments,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
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

    job = (
        db.table("jobs")
        .select("result_r2_keys")
        .eq("project_id", project_id)
        .eq("status", "completed")
        .order("created_at", desc=True)
        .limit(1)
        .maybe_single()
        .execute()
    )
    if not job.data or not job.data.get("result_r2_keys"):
        raise HTTPException(status_code=404, detail="완료된 작업이 없습니다")

    return job.data["result_r2_keys"]


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

    return SegmentsResponse(
        schema_version=payload.get("schema_version"),
        review_scope=payload.get("review_scope"),
        join_strategy=payload.get("join_strategy"),
        segments=payload.get("segments") or [],
        source_duration_ms=source_duration_ms,
    )


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

    # Collect git versions
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

    segments_json = [seg.model_dump() for seg in req.segments]
    stored_segments = {
        "schema_version": req.schema_version,
        "review_scope": req.review_scope,
        "join_strategy": req.join_strategy,
        "segments": segments_json,
    }

    # Atomic upsert using unique index on (project_id, evaluator_id)
    result = (
        db.table("evaluations")
        .upsert(
            {
                "project_id": project_id,
                "evaluator_id": user_id,
                "segments": stored_segments,
                "avid_version": avid_version,
                "eogum_version": eogum_version,
            },
            on_conflict="project_id,evaluator_id",
        )
        .execute()
    )

    return _evaluation_response_from_row(result.data[0])


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
    _, _, _, segments = _evaluation_metadata_from_segments(evaluation["segments"])

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
        ai = seg.get("ai", {})
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
