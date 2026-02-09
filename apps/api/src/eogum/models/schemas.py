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


# ── Downloads ──
class DownloadResponse(BaseModel):
    download_url: str
    filename: str


# ── Health ──
class HealthResponse(BaseModel):
    status: str
    version: str
