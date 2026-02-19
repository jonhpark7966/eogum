from datetime import datetime

from pydantic import BaseModel


# ── Upload ──
class PresignRequest(BaseModel):
    filename: str
    content_type: str
    size_bytes: int


class PresignResponse(BaseModel):
    upload_url: str
    r2_key: str


# ── Extra Sources (Multicam) ──
class ExtraSourceItem(BaseModel):
    r2_key: str
    filename: str
    size_bytes: int


class UpdateExtraSourcesRequest(BaseModel):
    extra_sources: list[ExtraSourceItem]


# ── Projects ──
class ProjectCreate(BaseModel):
    name: str
    cut_type: str  # subtitle_cut | podcast_cut
    language: str = "ko"
    source_r2_key: str
    source_filename: str
    source_duration_seconds: int
    source_size_bytes: int
    settings: dict = {}


class ProjectResponse(BaseModel):
    id: str
    name: str
    status: str
    cut_type: str
    language: str
    source_filename: str | None
    source_duration_seconds: int | None
    extra_sources: list[dict] = []
    created_at: datetime
    updated_at: datetime


class ProjectDetailResponse(ProjectResponse):
    source_r2_key: str | None
    source_size_bytes: int | None
    settings: dict
    jobs: list["JobResponse"] = []
    report: "EditReportResponse | None" = None


# ── Jobs ──
class JobResponse(BaseModel):
    id: str
    type: str
    status: str
    progress: int
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


# ── Credits ──
class CreditBalanceResponse(BaseModel):
    balance_seconds: int
    held_seconds: int
    available_seconds: int  # balance - held


class CreditTransactionResponse(BaseModel):
    id: str
    amount_seconds: int
    type: str
    description: str | None
    created_at: datetime


# ── Edit Reports ──
class EditReportResponse(BaseModel):
    total_duration_seconds: int
    cut_duration_seconds: int
    cut_percentage: float
    edit_summary: dict
    report_markdown: str


# ── Segments & Evaluation ──
class AiDecision(BaseModel):
    action: str  # "keep" | "cut"
    reason: str
    confidence: float
    note: str | None = None


class HumanDecision(BaseModel):
    action: str  # "keep" | "cut"
    reason: str
    note: str = ""


class SegmentWithDecision(BaseModel):
    index: int
    start_ms: int
    end_ms: int
    text: str
    ai: AiDecision | None = None


class EvalSegment(BaseModel):
    index: int
    start_ms: int
    end_ms: int
    text: str
    ai: AiDecision | None = None
    human: HumanDecision | None = None


class SegmentsResponse(BaseModel):
    segments: list[SegmentWithDecision]
    source_duration_ms: int


class EvaluationSave(BaseModel):
    segments: list[EvalSegment]


class EvaluationResponse(BaseModel):
    id: str
    project_id: str
    evaluator_id: str
    version: str
    avid_version: str | None
    eogum_version: str | None
    segments: list[EvalSegment]
    created_at: datetime
    updated_at: datetime


class VideoUrlResponse(BaseModel):
    video_url: str
    duration_ms: int


# ── Eval Report ──
class ConfusionMatrix(BaseModel):
    tp: int  # AI=cut, truth=cut (correct cut)
    tn: int  # AI=keep, truth=keep (correct keep)
    fp: int  # AI=cut, truth=keep (wrongly cut)
    fn: int  # AI=keep, truth=cut (missed cut)


class EvalMetrics(BaseModel):
    accuracy: float
    precision: float  # TP/(TP+FP) — AI cut 중 맞은 비율
    recall: float  # TP/(TP+FN) — 실제 cut 중 AI가 찾은 비율
    f1: float


class ReasonBreakdown(BaseModel):
    reason: str
    count: int
    total_ms: int


class DisagreementDetail(BaseModel):
    index: int
    start_ms: int
    end_ms: int
    text: str
    ai_action: str
    ai_reason: str
    human_action: str
    human_reason: str
    human_note: str


class EvalReportResponse(BaseModel):
    project_id: str
    avid_version: str | None
    eogum_version: str | None
    total_segments: int
    human_reviewed: int
    implicit_agree: int
    agreement_rate: float  # (total - disagreements) / total
    confusion: ConfusionMatrix
    metrics: EvalMetrics
    ai_cut_count: int
    ai_cut_ms: int
    truth_cut_count: int
    truth_cut_ms: int
    fp_reasons: list[ReasonBreakdown]  # AI가 잘못 cut한 이유별
    fn_reasons: list[ReasonBreakdown]  # AI가 놓친 cut의 이유별
    disagreements: list[DisagreementDetail]


# ── Multipart Upload ──
class MultipartInitiateRequest(BaseModel):
    filename: str
    content_type: str
    size_bytes: int


class MultipartPartUrl(BaseModel):
    part_number: int
    upload_url: str


class MultipartInitiateResponse(BaseModel):
    upload_id: str
    r2_key: str
    part_size: int
    part_urls: list[MultipartPartUrl]


class MultipartCompletePart(BaseModel):
    part_number: int
    etag: str


class MultipartCompleteRequest(BaseModel):
    r2_key: str
    upload_id: str
    parts: list[MultipartCompletePart]


# ── Downloads ──
class DownloadResponse(BaseModel):
    download_url: str
    filename: str


# ── Health ──
class HealthResponse(BaseModel):
    status: str
    version: str
