"""Evaluation routes for segment review and feedback."""

import json
import logging
import subprocess

from fastapi import APIRouter, Depends, HTTPException

from eogum.auth import get_user_id
from eogum.config import settings
from collections import Counter

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
from eogum.services.database import get_db
from eogum.services.r2 import download_to_bytes, generate_presigned_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}", tags=["evaluations"])


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
    """Get transcript segments merged with AI edit decisions from avid.json."""
    db = get_db()
    r2_keys = _get_completed_job(db, project_id, user_id)

    project_json_key = r2_keys.get("project_json")
    if not project_json_key:
        raise HTTPException(status_code=404, detail="프로젝트 JSON을 찾을 수 없습니다")

    # Download and parse avid.json
    raw = download_to_bytes(project_json_key)
    avid_data = json.loads(raw)

    transcription = avid_data.get("transcription")
    if not transcription or not transcription.get("segments"):
        raise HTTPException(status_code=404, detail="자막 데이터가 없습니다")

    edit_decisions = avid_data.get("edit_decisions", [])
    source_duration_ms = 0
    for sf in avid_data.get("source_files", []):
        info = sf.get("info", {})
        if info.get("duration_ms"):
            source_duration_ms = max(source_duration_ms, info["duration_ms"])

    # Merge segments with edit decisions (overlap-based matching)
    segments = []
    for i, seg in enumerate(transcription["segments"]):
        seg_start = seg["start_ms"]
        seg_end = seg["end_ms"]

        ai_decision = None
        for ed in edit_decisions:
            ed_range = ed.get("range", {})
            ed_start = ed_range.get("start_ms", 0)
            ed_end = ed_range.get("end_ms", 0)

            # Check overlap
            if ed_start < seg_end and ed_end > seg_start:
                edit_type = ed.get("edit_type", "")
                action = "cut" if edit_type in ("cut", "mute") else "keep"
                ai_decision = AiDecision(
                    action=action,
                    reason=ed.get("reason", ""),
                    confidence=ed.get("confidence", 0.0),
                    note=ed.get("note"),
                )
                break

        if ai_decision is None:
            ai_decision = AiDecision(action="keep", reason="", confidence=1.0)

        segments.append(SegmentWithDecision(
            index=i,
            start_ms=seg_start,
            end_ms=seg_end,
            text=seg.get("text", ""),
            ai=ai_decision,
        ))

    return SegmentsResponse(segments=segments, source_duration_ms=source_duration_ms)


@router.get("/video-url", response_model=VideoUrlResponse)
def get_video_url(project_id: str, user_id: str = Depends(get_user_id)):
    """Get presigned streaming URL for the preview video."""
    db = get_db()
    r2_keys = _get_completed_job(db, project_id, user_id)

    preview_key = r2_keys.get("preview")
    if not preview_key:
        raise HTTPException(status_code=404, detail="프리뷰 영상이 없습니다")

    # Get source duration
    project = (
        db.table("projects")
        .select("source_duration_seconds")
        .eq("id", project_id)
        .single()
        .execute()
    )
    duration_ms = (project.data.get("source_duration_seconds") or 0) * 1000

    video_url = generate_presigned_stream(preview_key)
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

    return result.data[0]


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
    avid_version = None
    eogum_version = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(settings.avid_cli_path),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            avid_version = result.stdout.strip()
    except Exception:
        pass

    try:
        # eogum repo is the parent of apps/api
        eogum_repo = settings.avid_cli_path.parent.parent.parent / "eogum"
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

    # Upsert: try update first, then insert
    existing = (
        db.table("evaluations")
        .select("id")
        .eq("project_id", project_id)
        .eq("evaluator_id", user_id)
        .limit(1)
        .execute()
    )

    if existing.data:
        result = (
            db.table("evaluations")
            .update({
                "segments": segments_json,
                "avid_version": avid_version,
                "eogum_version": eogum_version,
            })
            .eq("id", existing.data[0]["id"])
            .execute()
        )
        data = result.data[0]
    else:
        result = (
            db.table("evaluations")
            .insert({
                "project_id": project_id,
                "evaluator_id": user_id,
                "segments": segments_json,
                "avid_version": avid_version,
                "eogum_version": eogum_version,
            })
            .execute()
        )
        data = result.data[0]

    return data


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
    segments = evaluation["segments"]

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
