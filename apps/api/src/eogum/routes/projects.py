import json
import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from eogum.auth import CurrentUser, get_current_user, get_optional_current_user, get_user_id
from eogum.models.schemas import (
    ProjectCreate,
    ProjectDetailResponse,
    ProjectResponse,
    ProjectVariantCreate,
    SourceDeriveRequest,
    UpdateExtraSourcesRequest,
    UpdateMulticamSettingsRequest,
)
from eogum.public_access import is_public_project_id
from eogum.services.artifacts import get_latest_artifact_job
from eogum.services.credit import get_balance
from eogum.services.database import get_db
from eogum.services.job_runner import (
    create_cut_decision_job,
    create_initial_job,
    create_source_derive_job,
    enqueue,
    enqueue_cut_decision,
    enqueue_reprocess,
    enqueue_source_derive,
)
from eogum.services.r2 import delete_objects, download_to_bytes, object_exists
from eogum.services import source_derivatives
from eogum.services.source_cache import lookup_source_asset, upsert_source_asset

router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger(__name__)

ALLOWED_EDIT_INTENSITIES = {"light", "normal", "heavy"}
ALLOWED_EDIT_DECISION_VERSIONS = {"legacy", "boundary_aware_v1"}
ALLOWED_SEGMENTATION_BOUNDARY_RULES = {
    "word_boundary",
    "midpoint_gap",
    "low_energy_gap_v1",
}
ALLOWED_MULTICAM_SWITCHING = {
    "none",
    "follow_speaker",
    "conservative_follow_speaker",
}
EDIT_INTENSITY_LABELS = {
    "light": "적게 편집",
    "normal": "일반 편집",
    "heavy": "많이 편집",
}


def _extra_sources_hash(extra_sources: list[dict]) -> str | None:
    if not extra_sources:
        return None
    normalized = [
        {
            "r2_key": item.get("r2_key"),
            "filename": item.get("filename"),
            "size_bytes": item.get("size_bytes"),
            "offset_ms": item.get("offset_ms"),
        }
        for item in extra_sources
    ]
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pending_multicam_state(project: dict, extra_sources: list[dict]) -> dict:
    current = project.get("multicam_state") or {}
    desired_hash = _extra_sources_hash(extra_sources)
    if not desired_hash:
        return {
            "status": "not_applied",
            "desired_sources_hash": None,
            "applied_sources_hash": None,
            "source_count": 0,
            "job_id": None,
            "applied_at": None,
            "error": None,
        }

    applied_hash = current.get("applied_sources_hash")
    status_value = "applied" if applied_hash == desired_hash else "pending_apply"
    return {
        **current,
        "status": status_value,
        "desired_sources_hash": desired_hash,
        "source_count": len(extra_sources),
        "error": None,
    }


def _valid_multicam_source_keys(project: dict) -> set[str]:
    keys = {"primary"}
    for index, _source in enumerate(project.get("extra_sources") or []):
        keys.add(f"extra:{index}")
    return keys


def _validate_multicam_source_key(project: dict, source_key: str, field_name: str) -> None:
    if source_key not in _valid_multicam_source_keys(project):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name}에 알 수 없는 멀티캠 source_key가 있습니다: {source_key}",
        )


def _merge_multicam_settings(
    project: dict,
    req: UpdateMulticamSettingsRequest,
) -> dict:
    merged = dict(project.get("settings") or {})

    if req.multicam_switching is not None:
        if req.multicam_switching not in ALLOWED_MULTICAM_SWITCHING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="multicam_switching은 none, follow_speaker, conservative_follow_speaker 중 하나여야 합니다",
            )
        merged["multicam_switching"] = req.multicam_switching

    if req.audio_source_key is not None:
        _validate_multicam_source_key(project, req.audio_source_key, "audio_source_key")
        merged["audio_source_key"] = req.audio_source_key

    if req.speaker_source_map is not None:
        speaker_source_map: dict[str, str] = {}
        for speaker, source_key in req.speaker_source_map.items():
            normalized_speaker = str(speaker).strip()
            normalized_source_key = str(source_key).strip()
            if not normalized_speaker or not normalized_source_key:
                continue
            _validate_multicam_source_key(
                project,
                normalized_source_key,
                f"speaker_source_map[{normalized_speaker}]",
            )
            speaker_source_map[normalized_speaker] = normalized_source_key
        merged["speaker_source_map"] = speaker_source_map

    if req.multicam_source_labels is not None:
        labels: dict[str, dict] = {}
        for source_key, raw_label in req.multicam_source_labels.items():
            normalized_source_key = str(source_key).strip()
            _validate_multicam_source_key(
                project,
                normalized_source_key,
                "multicam_source_labels",
            )
            label = raw_label if isinstance(raw_label, dict) else {}
            display_id = str(label.get("display_id") or "").strip()
            display_name = str(label.get("display_name") or "").strip()
            labels[normalized_source_key] = {
                "display_id": display_id,
                "display_name": display_name,
            }
        merged["multicam_source_labels"] = labels

    return merged


def _first_row(result) -> dict | None:
    data = getattr(result, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None


def _project_access_query(db, current_user: CurrentUser, select: str = "*"):
    query = db.table("projects").select(select)
    if not current_user.is_admin:
        query = query.eq("user_id", current_user.id)
    return query


def _select_with_access_columns(select: str) -> str:
    if select.strip() == "*":
        return select
    columns = [column.strip() for column in select.split(",") if column.strip()]
    for required in ("id", "user_id"):
        if required not in columns:
            columns.append(required)
    return ", ".join(columns)


def _has_project_owner_access(project: dict, current_user: CurrentUser | None) -> bool:
    if current_user is None:
        return False
    return current_user.is_admin or project.get("user_id") == current_user.id


def _get_accessible_project(
    db,
    project_id: str,
    current_user: CurrentUser | None,
    select: str = "*",
    *,
    allow_public_read: bool = False,
) -> dict:
    project = (
        db.table("projects")
        .select(_select_with_access_columns(select))
        .eq("id", project_id)
        .single()
        .execute()
    )
    if not project.data:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")
    if _has_project_owner_access(project.data, current_user):
        return project.data
    if allow_public_read and is_public_project_id(project_id):
        return project.data
    raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")


def _public_source_derived(source_derived: dict | None) -> dict:
    if not isinstance(source_derived, dict):
        return {}
    public_value = dict(source_derived)
    public_value.pop("media_info_r2_key", None)
    public_value.pop("audio_proxy_r2_key", None)
    return public_value


def _public_extra_sources(extra_sources: list[dict]) -> list[dict]:
    public_sources = []
    for index, source in enumerate(extra_sources):
        public_source = dict(source)
        public_source["r2_key"] = f"public-extra-source-{index}"
        public_source.pop("source_sha256", None)
        public_source["derived"] = _public_source_derived(public_source.get("derived"))
        public_sources.append(public_source)
    return public_sources


def _sanitize_public_project_detail(project: dict) -> dict:
    public_project = dict(project)
    public_project["source_r2_key"] = None
    public_project["source_derived"] = _public_source_derived(public_project.get("source_derived"))
    public_project["extra_sources"] = _public_extra_sources(public_project.get("extra_sources") or [])
    public_jobs = []
    for job in public_project.get("jobs") or []:
        public_job = dict(job)
        public_job["external_task_ids"] = {}
        public_job["result_r2_keys"] = None
        public_jobs.append(public_job)
    public_project["jobs"] = public_jobs
    return public_project


def _upsert_project_source_asset_best_effort(db, project: dict) -> None:
    source_sha256 = project.get("source_sha256")
    source_size_bytes = project.get("source_size_bytes")
    source_r2_key = project.get("source_r2_key")
    if not source_sha256 or source_size_bytes is None or not source_r2_key:
        return

    try:
        upsert_source_asset(
            db,
            sha256=source_sha256,
            size_bytes=int(source_size_bytes),
            r2_key=source_r2_key,
            filename=project.get("source_filename"),
            duration_seconds=project.get("source_duration_seconds"),
        )
    except Exception:
        logger.exception("Failed to upsert source asset for project %s", project.get("id"))


def _cached_source_derived(db, *, sha256: str | None, size_bytes: int | None) -> dict:
    if not sha256 or size_bytes is None:
        return {}
    try:
        asset = lookup_source_asset(db, sha256=sha256, size_bytes=int(size_bytes))
    except Exception:
        logger.exception("Failed to lookup cached source derivative")
        return {}
    snapshot = source_derivatives.normalize_asset_row(asset)
    return snapshot if source_derivatives.is_ready(snapshot) else {}


def _assert_project_source_exists(project: dict) -> None:
    source_r2_key = project.get("source_r2_key")
    if not source_r2_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="원본 소스 정보가 없어 멀티캠을 적용할 수 없습니다",
        )

    try:
        exists = object_exists(source_r2_key)
    except Exception as exc:
        logger.exception("Failed to check source object for project %s", project.get("id"))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="원본 파일 상태를 확인할 수 없습니다. 잠시 후 다시 시도해주세요.",
        ) from exc

    if not exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="원본 영상 파일이 저장소에 없어 멀티캠을 적용할 수 없습니다. 원본 파일을 다시 업로드해야 합니다.",
        )


def _source_r2_key_is_shared(db, *, r2_key: str | None, project_id: str) -> bool:
    if not r2_key:
        return False

    try:
        project_ref = (
            db.table("projects")
            .select("id")
            .eq("source_r2_key", r2_key)
            .neq("id", project_id)
            .limit(1)
            .execute()
        )
        if _first_row(project_ref):
            return True

        cache_ref = (
            db.table("source_assets")
            .select("id")
            .eq("r2_key", r2_key)
            .limit(1)
            .execute()
        )
        return _first_row(cache_ref) is not None
    except Exception:
        logger.exception("Failed to check source references for R2 key %s", r2_key)
        return True


def _create_initial_job_or_fail(db, project: dict) -> dict:
    try:
        return create_initial_job(db, project)
    except Exception as exc:
        logger.exception("Failed to create initial job for project %s", project.get("id"))
        try:
            db.table("projects").update({"status": "failed"}).eq("id", project["id"]).execute()
        except Exception:
            logger.exception("Failed to mark project %s failed after job creation error", project.get("id"))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="프로젝트 처리 작업 등록에 실패했습니다",
        ) from exc


def _mark_derivatives_queued(project: dict, source_keys: list[str]) -> dict:
    updated = dict(project)
    for source_key in source_keys:
        ref = source_derivatives.source_ref(updated, source_key)
        if source_derivatives.is_ready(ref.get("derived") or {}):
            continue
        updated = source_derivatives.set_project_source_snapshot(
            updated,
            source_key,
            source_derivatives.queued_snapshot(),
        )
    return updated


def _queue_source_derive_if_needed(
    db,
    project: dict,
    *,
    source_keys: list[str],
    force: bool = False,
) -> None:
    source_keys = [key for key in source_keys if key]
    if not source_keys:
        return
    job = create_source_derive_job(db, project, source_keys=source_keys, force=force)
    enqueue_source_derive(project["id"], job["id"])


def _extra_sources_with_preserved_derivatives(project: dict, incoming: list[dict]) -> list[dict]:
    existing_by_key = {
        item.get("r2_key"): item
        for item in (project.get("extra_sources") or [])
        if item.get("r2_key")
    }
    merged: list[dict] = []
    for source in incoming:
        normalized = dict(source)
        existing = existing_by_key.get(normalized.get("r2_key")) or {}
        if existing.get("source_sha256"):
            normalized["source_sha256"] = existing["source_sha256"]
        if existing.get("derived"):
            normalized["derived"] = existing["derived"]
        elif not normalized.get("derived"):
            normalized["derived"] = {}
        merged.append(normalized)
    return merged


def _ensure_multicam_derivatives_ready_or_raise(db, project: dict) -> None:
    missing = source_derivatives.source_keys_needing_derivatives(project)
    if not missing:
        return

    queued_project = _mark_derivatives_queued(project, missing)
    update_values = {
        "source_derived": queued_project.get("source_derived") or {},
        "extra_sources": queued_project.get("extra_sources") or [],
    }
    updated = (
        db.table("projects")
        .update(update_values)
        .eq("id", project["id"])
        .execute()
        .data[0]
    )
    _queue_source_derive_if_needed(db, updated, source_keys=missing)
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="멀티캠 적용 준비 중입니다. 오디오 프록시 생성이 끝난 뒤 다시 시도해주세요.",
    )



def _validate_project_settings(req: ProjectCreate) -> None:
    settings_value = dict(req.settings or {})
    if "output_target_duration_minutes" in settings_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="결과 길이 설정은 더 이상 지원하지 않습니다. edit_intensity를 사용하세요",
        )

    edit_intensity = settings_value.get("edit_intensity", "normal")
    if not isinstance(edit_intensity, str) or edit_intensity not in ALLOWED_EDIT_INTENSITIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="편집 강도는 light, normal, heavy 중 하나여야 합니다",
        )
    settings_value["edit_intensity"] = edit_intensity

    edit_decision_version = settings_value.get("edit_decision_version", "legacy")
    if (
        not isinstance(edit_decision_version, str)
        or edit_decision_version not in ALLOWED_EDIT_DECISION_VERSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Edit Decision 방식은 legacy 또는 boundary_aware_v1 중 하나여야 합니다",
        )
    settings_value["edit_decision_version"] = edit_decision_version

    segmentation_boundary_rule = settings_value.get("segmentation_boundary_rule", "word_boundary")
    if (
        not isinstance(segmentation_boundary_rule, str)
        or segmentation_boundary_rule not in ALLOWED_SEGMENTATION_BOUNDARY_RULES
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Segmentation Boundary Rule은 word_boundary, midpoint_gap, low_energy_gap_v1 중 하나여야 합니다",
        )
    settings_value["segmentation_boundary_rule"] = segmentation_boundary_rule
    req.settings = settings_value

    if settings_value.get("overlap_protection_enabled") is None:
        settings_value["overlap_protection_enabled"] = False

    for key in (
        "diarize",
        "tag_audio_events",
        "use_llm_segmentation",
        "use_llm_refinement",
        "overlap_protection_enabled",
    ):
        value = settings_value.get(key)
        if value is not None and not isinstance(value, bool):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{key} 옵션은 true 또는 false여야 합니다",
            )

    num_speakers = settings_value.get("num_speakers")
    if num_speakers in (None, ""):
        return
    if isinstance(num_speakers, bool):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="예상 화자 수는 1에서 32 사이 숫자여야 합니다",
        )
    try:
        speaker_count = int(num_speakers)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="예상 화자 수는 1에서 32 사이 숫자여야 합니다",
        ) from None
    if not 1 <= speaker_count <= 32:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="예상 화자 수는 1에서 32 사이 숫자여야 합니다",
        )


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(req: ProjectCreate, user_id: str = Depends(get_user_id)):
    _validate_project_settings(req)

    # Check credits
    balance = get_balance(user_id)
    if balance["available_seconds"] < req.source_duration_seconds:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"크레딧이 부족합니다. 필요: {req.source_duration_seconds}초, 사용 가능: {balance['available_seconds']}초",
        )

    db = get_db()
    source_derived = _cached_source_derived(
        db,
        sha256=req.source_sha256,
        size_bytes=req.source_size_bytes,
    )

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
        "source_sha256": req.source_sha256,
        "source_derived": source_derived,
        "settings": req.settings,
    }).execute().data[0]

    job = _create_initial_job_or_fail(db, project)
    _upsert_project_source_asset_best_effort(db, project)

    # Enqueue for processing only after the durable job exists.
    enqueue(project["id"], job["id"])

    return project


@router.post("/{project_id}/variants", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project_variant(project_id: str, req: ProjectVariantCreate, current_user: CurrentUser = Depends(get_current_user)):
    if req.edit_intensity not in ALLOWED_EDIT_INTENSITIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="편집 강도는 light, normal, heavy 중 하나여야 합니다",
        )

    requested_edit_decision_version = req.edit_decision_version
    if requested_edit_decision_version is not None and requested_edit_decision_version not in ALLOWED_EDIT_DECISION_VERSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Edit Decision 방식은 legacy 또는 boundary_aware_v1 중 하나여야 합니다",
        )

    requested_segmentation_boundary_rule = req.segmentation_boundary_rule
    if (
        requested_segmentation_boundary_rule is not None
        and requested_segmentation_boundary_rule not in ALLOWED_SEGMENTATION_BOUNDARY_RULES
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Segmentation Boundary Rule은 word_boundary, midpoint_gap, low_energy_gap_v1 중 하나여야 합니다",
        )

    db = get_db()
    source_project_data = _get_accessible_project(db, project_id, current_user)
    source_owner_id = source_project_data["user_id"]

    if source_project_data["status"] != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="완료된 프로젝트만 새 편집 버전을 만들 수 있습니다",
        )

    source_settings = dict(source_project_data.get("settings") or {})

    duration = int(source_project_data.get("source_duration_seconds") or 0)
    source_size_bytes = source_project_data.get("source_size_bytes")
    source_r2_key = source_project_data.get("source_r2_key")
    if not source_r2_key or source_size_bytes is None or duration <= 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="원본 소스 정보가 없어 새 편집 강도 프로젝트를 만들 수 없습니다",
        )

    balance = get_balance(source_owner_id)
    if balance["available_seconds"] < duration:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="크레딧이 부족합니다. 필요: %d초, 사용 가능: %d초" % (duration, balance["available_seconds"]),
        )

    source_edit_decision_version = source_settings.get("edit_decision_version", "legacy")
    if source_edit_decision_version not in ALLOWED_EDIT_DECISION_VERSIONS:
        source_edit_decision_version = "legacy"
    edit_decision_version = requested_edit_decision_version or source_edit_decision_version
    source_segmentation_boundary_rule = source_settings.get(
        "segmentation_boundary_rule",
        "word_boundary",
    )
    if source_segmentation_boundary_rule not in ALLOWED_SEGMENTATION_BOUNDARY_RULES:
        source_segmentation_boundary_rule = "word_boundary"
    segmentation_boundary_rule = (
        requested_segmentation_boundary_rule or source_segmentation_boundary_rule
    )
    if req.overlap_protection_enabled is not None and not isinstance(req.overlap_protection_enabled, bool):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="overlap_protection_enabled 옵션은 true 또는 false여야 합니다",
        )
    overlap_protection_enabled = (
        req.overlap_protection_enabled
        if req.overlap_protection_enabled is not None
        else bool(source_settings.get("overlap_protection_enabled", False))
    )

    variant_settings = {
        **source_settings,
        "edit_intensity": req.edit_intensity,
        "edit_decision_version": edit_decision_version,
        "segmentation_boundary_rule": segmentation_boundary_rule,
        "overlap_protection_enabled": overlap_protection_enabled,
        "bypass_llm_segmentation_cache": True,
    }
    for internal_key in (
        "reused_transcription_srt_r2_key",
        "reused_transcription_from_project_id",
        "reused_transcription_from_job_id",
    ):
        variant_settings.pop(internal_key, None)
    variant_name = (req.name or "").strip()
    if not variant_name:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        variant_name = "{} - {} {}".format(
            source_project_data["name"],
            EDIT_INTENSITY_LABELS[req.edit_intensity],
            timestamp,
        )

    source_derived = source_project_data.get("source_derived") or _cached_source_derived(
        db,
        sha256=source_project_data.get("source_sha256"),
        size_bytes=int(source_size_bytes),
    )

    project = db.table("projects").insert({
        "user_id": source_owner_id,
        "name": variant_name,
        "status": "queued",
        "cut_type": source_project_data["cut_type"],
        "language": source_project_data["language"],
        "source_r2_key": source_r2_key,
        "source_filename": source_project_data.get("source_filename"),
        "source_duration_seconds": duration,
        "source_size_bytes": int(source_size_bytes),
        "source_sha256": source_project_data.get("source_sha256"),
        "source_derived": source_derived,
        "settings": variant_settings,
    }).execute().data[0]

    _upsert_project_source_asset_best_effort(db, project)

    job = _create_initial_job_or_fail(db, project)
    enqueue(project["id"], job["id"])
    return project


@router.get("", response_model=list[ProjectResponse])
def list_projects(current_user: CurrentUser = Depends(get_current_user)):
    db = get_db()
    result = _project_access_query(db, current_user).order("created_at", desc=True).execute()
    return result.data


@router.get("/{project_id}", response_model=ProjectDetailResponse)
def get_project(
    project_id: str,
    current_user: CurrentUser | None = Depends(get_optional_current_user),
):
    db = get_db()

    project_data = _get_accessible_project(
        db,
        project_id,
        current_user,
        allow_public_read=True,
    )

    jobs = db.table("jobs").select("*").eq("project_id", project_id).order("created_at").execute()
    report = db.table("edit_reports").select("*").eq("project_id", project_id).limit(1).execute()

    data = dict(project_data)
    data["jobs"] = jobs.data
    data["report"] = report.data[0] if report.data else None
    if not _has_project_owner_access(project_data, current_user):
        data = _sanitize_public_project_detail(data)
    return data


@router.post("/{project_id}/retry", response_model=ProjectResponse)
def retry_project(project_id: str, current_user: CurrentUser = Depends(get_current_user)):
    db = get_db()

    project_data = _get_accessible_project(db, project_id, current_user)
    owner_user_id = project_data["user_id"]

    if project_data["status"] != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="실패한 프로젝트만 재시도할 수 있습니다",
        )

    # Check credits
    duration = project_data["source_duration_seconds"]
    balance = get_balance(owner_user_id)
    if balance["available_seconds"] < duration:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"크레딧이 부족합니다. 필요: {duration}초, 사용 가능: {balance['available_seconds']}초",
        )

    # Clean up old failed jobs and reports
    db.table("jobs").delete().eq("project_id", project_id).execute()
    db.table("edit_reports").delete().eq("project_id", project_id).execute()

    # Reset project status and create a durable retry job before enqueueing.
    updated = db.table("projects").update({"status": "queued"}).eq("id", project_id).execute().data[0]
    job = _create_initial_job_or_fail(db, updated)

    # Enqueue for processing
    enqueue(project_id, job["id"])

    return updated


@router.post("/{project_id}/cut-decision", response_model=ProjectResponse)
def rerun_cut_decision(project_id: str, current_user: CurrentUser = Depends(get_current_user)):
    db = get_db()

    project_data = _get_accessible_project(db, project_id, current_user)
    owner_user_id = project_data["user_id"]

    if project_data["status"] in {"queued", "processing"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="진행 중인 프로젝트는 cut decision을 다시 실행할 수 없습니다",
        )

    active_job = (
        db.table("jobs")
        .select("id, type")
        .eq("project_id", project_id)
        .eq("user_id", owner_user_id)
        .in_("status", ["queued", "pending", "running", "cancel_requested"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if _first_row(active_job):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 진행 중인 작업이 있습니다",
        )

    multicam_status = (project_data.get("multicam_state") or {}).get("status")
    if project_data.get("extra_sources") and multicam_status == "pending_apply":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="등록된 멀티캠 변경사항을 먼저 적용하거나 제거한 뒤 cut decision을 다시 실행할 수 있습니다",
        )

    _assert_project_source_exists(project_data)

    artifact_job = get_latest_artifact_job(
        db,
        project_id,
        user_id=owner_user_id,
        select="id, result_r2_keys",
    )
    if not artifact_job:
        raise HTTPException(status_code=404, detail="완료된 작업이 없습니다. 전체 재처리가 필요합니다.")

    result_keys = artifact_job["result_r2_keys"] or {}
    if not result_keys.get("project_json"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="기존 refined segment 정보가 없어 cut decision만 다시 실행할 수 없습니다",
        )

    job = create_cut_decision_job(db, project_data)
    updated = (
        db.table("projects")
        .update({"status": "processing"})
        .eq("id", project_id)
        .execute()
        .data[0]
    )
    enqueue_cut_decision(project_id, job["id"])
    return updated


@router.put("/{project_id}/extra-sources", response_model=ProjectResponse)
def update_extra_sources(
    project_id: str,
    req: UpdateExtraSourcesRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = get_db()

    project_data = _get_accessible_project(db, project_id, current_user)

    if project_data["status"] not in ("completed", "failed", "reprocess_failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="완료 또는 실패한 프로젝트만 추가 소스를 설정할 수 있습니다",
        )

    extra_sources = _extra_sources_with_preserved_derivatives(
        project_data,
        [s.model_dump() for s in req.extra_sources],
    )
    pending_project = {
        **project_data,
        "extra_sources": extra_sources,
    }
    source_keys = (
        source_derivatives.source_keys_needing_derivatives(pending_project)
        if extra_sources else []
    )
    pending_project = _mark_derivatives_queued(pending_project, source_keys)
    extra_sources = pending_project.get("extra_sources") or []
    updated = (
        db.table("projects")
        .update({
            "source_derived": pending_project.get("source_derived") or {},
            "extra_sources": extra_sources,
            "multicam_state": _pending_multicam_state(project_data, extra_sources),
        })
        .eq("id", project_id)
        .execute()
        .data[0]
    )
    _queue_source_derive_if_needed(db, updated, source_keys=source_keys)
    return updated


@router.post("/{project_id}/extra-sources/derive", response_model=ProjectResponse)
def retry_extra_source_derivatives(
    project_id: str,
    req: SourceDeriveRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = get_db()
    project_data = _get_accessible_project(db, project_id, current_user)

    if project_data["status"] in {"queued", "processing"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="처리 중인 프로젝트의 파생 파일은 재생성할 수 없습니다",
        )
    if not project_data.get("extra_sources"):
        return project_data

    source_keys = source_derivatives.source_keys_needing_derivatives(project_data, force=req.force)
    if not source_keys:
        return project_data

    queued_project = _mark_derivatives_queued(project_data, source_keys)
    updated = (
        db.table("projects")
        .update({
            "source_derived": queued_project.get("source_derived") or {},
            "extra_sources": queued_project.get("extra_sources") or [],
        })
        .eq("id", project_id)
        .execute()
        .data[0]
    )
    _queue_source_derive_if_needed(db, updated, source_keys=source_keys, force=req.force)
    return updated


@router.put("/{project_id}/multicam-settings", response_model=ProjectResponse)
def update_multicam_settings(
    project_id: str,
    req: UpdateMulticamSettingsRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = get_db()
    project_data = _get_accessible_project(db, project_id, current_user)

    if project_data["status"] in {"queued", "processing"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="처리 중인 프로젝트의 멀티캠 설정은 변경할 수 없습니다",
        )

    settings_value = _merge_multicam_settings(project_data, req)
    update_values = {"settings": settings_value}
    requires_reexport = (
        req.multicam_switching is not None
        or req.speaker_source_map is not None
        or req.audio_source_key is not None
    )
    if requires_reexport and project_data.get("extra_sources"):
        multicam_state = _pending_multicam_state(
            project_data,
            project_data.get("extra_sources") or [],
        )
        multicam_state["status"] = "pending_apply"
        update_values["multicam_state"] = multicam_state

    updated = (
        db.table("projects")
        .update(update_values)
        .eq("id", project_id)
        .execute()
        .data[0]
    )
    return updated


@router.post("/{project_id}/multicam", response_model=ProjectResponse)
def multicam_reprocess(project_id: str, current_user: CurrentUser = Depends(get_current_user)):
    """Queue project reprocess via split avid-cli commands."""
    db = get_db()

    project_data = _get_accessible_project(db, project_id, current_user)
    owner_user_id = project_data["user_id"]

    if project_data["status"] not in ("completed", "failed", "reprocess_failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="완료 또는 실패한 프로젝트만 재처리할 수 있습니다",
        )

    existing_reprocess = (
        db.table("jobs")
        .select("id")
        .eq("project_id", project_id)
        .eq("type", "reprocess_multicam")
        .in_("status", ["pending", "running"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if _first_row(existing_reprocess):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 재처리 작업이 진행 중입니다",
        )

    job = get_latest_artifact_job(db, project_id, select="id, result_r2_keys")
    if not job:
        raise HTTPException(status_code=404, detail="완료된 작업이 없습니다. 전체 재처리가 필요합니다.")

    r2_keys = job["result_r2_keys"]
    project_json_key = r2_keys.get("project_json")
    if not project_json_key:
        raise HTTPException(status_code=404, detail="프로젝트 JSON이 없습니다. 전체 재처리가 필요합니다.")

    project_json_bytes = download_to_bytes(project_json_key)
    try:
        stored_project_json = json.loads(project_json_bytes.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="저장된 프로젝트 JSON을 읽을 수 없습니다") from exc

    eval_result = (
        db.table("evaluations")
        .select("segments")
        .eq("project_id", project_id)
        .eq("evaluator_id", owner_user_id)
        .limit(1)
        .execute()
    )
    evaluation_payload = eval_result.data[0]["segments"] if eval_result.data else None
    if isinstance(evaluation_payload, dict):
        eval_segments = evaluation_payload.get("segments") or []
    else:
        eval_segments = evaluation_payload

    has_extra_sources = bool(project_data.get("extra_sources"))
    current_project_has_extra_sources = len(stored_project_json.get("source_files") or []) > 1
    if not eval_segments and not has_extra_sources and not current_project_has_extra_sources:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="평가 데이터 또는 적용할 extra source 변경이 필요합니다",
        )

    if has_extra_sources:
        _ensure_multicam_derivatives_ready_or_raise(db, project_data)

    desired_hash = _extra_sources_hash(project_data.get("extra_sources") or [])
    queued_job = db.table("jobs").insert({
        "project_id": project_id,
        "user_id": owner_user_id,
        "type": "reprocess_multicam",
        "status": "pending",
        "progress": 0,
    }).execute().data[0]
    multicam_state = {
        **(project_data.get("multicam_state") or {}),
        "status": "queued",
        "desired_sources_hash": desired_hash,
        "source_count": len(project_data.get("extra_sources") or []),
        "job_id": queued_job["id"],
        "error": None,
    }
    db.table("projects").update({"status": "processing", "multicam_state": multicam_state}).eq("id", project_id).execute()
    enqueue_reprocess(project_id, queued_job["id"])

    return db.table("projects").select("*").eq("id", project_id).single().execute().data


@router.post("/{project_id}/multicam/cancel", response_model=ProjectResponse)
def cancel_multicam_reprocess(project_id: str, current_user: CurrentUser = Depends(get_current_user)):
    db = get_db()

    project_data = _get_accessible_project(db, project_id, current_user)
    owner_user_id = project_data["user_id"]

    latest = (
        db.table("jobs")
        .select("id, status")
        .eq("project_id", project_id)
        .eq("user_id", owner_user_id)
        .eq("type", "reprocess_multicam")
        .in_("status", ["pending", "running", "cancel_requested"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    latest_job = _first_row(latest)
    if not latest_job:
        raise HTTPException(status_code=404, detail="취소할 멀티캠 작업이 없습니다")

    job_status = latest_job["status"]
    job_id = latest_job["id"]
    next_job_status = "canceled" if job_status == "pending" else "cancel_requested"
    job_update = {"status": next_job_status}
    if next_job_status == "canceled":
        job_update.update({"progress": 0, "completed_at": "now()"})
    db.table("jobs").update(job_update).eq("id", job_id).execute()

    state_status = "canceled" if next_job_status == "canceled" else "canceling"
    multicam_state = {
        **(project_data.get("multicam_state") or {}),
        "status": state_status,
        "job_id": job_id,
        "error": None,
    }
    updated = (
        db.table("projects")
        .update({"status": "completed", "multicam_state": multicam_state})
        .eq("id", project_id)
        .execute()
        .data[0]
    )
    return updated


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, current_user: CurrentUser = Depends(get_current_user)):
    db = get_db()

    project_data = _get_accessible_project(db, project_id, current_user)

    if project_data["status"] in {"queued", "processing"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="처리 중인 프로젝트는 완료 또는 취소 후 삭제할 수 있습니다",
        )

    active_job = (
        db.table("jobs")
        .select("id")
        .eq("project_id", project_id)
        .in_("status", ["pending", "running", "cancel_requested"])
        .limit(1)
        .execute()
    )
    if active_job.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="진행 중인 작업이 있어 프로젝트를 삭제할 수 없습니다",
        )

    source_r2_key = project_data.get("source_r2_key")
    r2_keys = []
    if source_r2_key:
        if _source_r2_key_is_shared(db, r2_key=source_r2_key, project_id=project_id):
            logger.info("Skipping shared source object cleanup for project %s: %s", project_id, source_r2_key)
        else:
            r2_keys.append(source_r2_key)
    r2_keys.extend(src.get("r2_key") for src in (project_data.get("extra_sources") or []))
    jobs = db.table("jobs").select("result_r2_keys").eq("project_id", project_id).execute()
    for job in jobs.data or []:
        for key in (job.get("result_r2_keys") or {}).values():
            if isinstance(key, str):
                r2_keys.append(key)
    try:
        delete_objects([key for key in r2_keys if key])
    except Exception:
        logger.exception("Best-effort R2 cleanup failed for project %s", project_id)

    db.table("projects").delete().eq("id", project_id).execute()
