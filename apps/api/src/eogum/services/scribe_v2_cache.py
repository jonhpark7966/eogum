"""Database and key helpers for the global Scribe V2 raw cache."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any


SCRIBE_API_VERSION = "scribe_v2"
AUTO_DETECT_LANGUAGE = "auto"
RUNNING_POLL_INTERVAL_SECONDS = 5.0
RUNNING_POLL_TIMEOUT_SECONDS = 2 * 60 * 60.0
_EXPECTED_VALUE_UNSET = object()


@dataclass(frozen=True)
class ScribeV2CacheParams:
    source_sha256: str
    source_size_bytes: int
    language: str
    diarize: bool
    num_speakers: int | None
    tag_audio_events: bool
    scribe_api_version: str = SCRIBE_API_VERSION


def language_hint(language: object) -> str | None:
    """Convert the persisted project language into a Scribe language hint."""
    normalized = str(language or "").strip()
    if not normalized or normalized.lower() == AUTO_DETECT_LANGUAGE:
        return None
    return normalized


def cache_language(language: object) -> str:
    """Return the non-null language value used by the raw Scribe cache row."""
    return language_hint(language) or ""


def build_scribe_v2_cache_key(params: ScribeV2CacheParams) -> str:
    payload = {
        "source_sha256": params.source_sha256,
        "source_size_bytes": params.source_size_bytes,
        "language": params.language,
        "diarize": params.diarize,
        "num_speakers": params.num_speakers,
        "tag_audio_events": params.tag_audio_events,
        "scribe_api_version": params.scribe_api_version,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def raw_json_r2_key(cache_key: str) -> str:
    return f"cache/scribe-v2/{cache_key}/raw.json"


def raw_srt_r2_key(cache_key: str) -> str:
    return f"cache/scribe-v2/{cache_key}/raw.srt"


def attempt_raw_json_r2_key(cache_key: str, owner_token: str) -> str:
    return f"cache/scribe-v2/{cache_key}/attempts/{owner_token}/raw.json"


def attempt_raw_srt_r2_key(cache_key: str, owner_token: str) -> str:
    return f"cache/scribe-v2/{cache_key}/attempts/{owner_token}/raw.srt"


def get_cache_entry(db, cache_key: str) -> dict | None:
    result = (
        db.table("scribe_v2_cache_entries")
        .select("*")
        .eq("cache_key", cache_key)
        .limit(1)
        .execute()
    )
    data = getattr(result, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None


def new_owner_token() -> str:
    return str(uuid.uuid4())


def create_running_entry(
    db,
    *,
    cache_key: str,
    params: ScribeV2CacheParams,
    owner_token: str,
) -> dict | None:
    payload = {
        "cache_key": cache_key,
        "source_sha256": params.source_sha256,
        "source_size_bytes": params.source_size_bytes,
        "language": params.language,
        "diarize": params.diarize,
        "num_speakers": params.num_speakers,
        "tag_audio_events": params.tag_audio_events,
        "scribe_api_version": params.scribe_api_version,
        "status": "running",
        "owner_token": owner_token,
        "error_message": None,
        "failure_kind": None,
        "retryable": False,
        "resubmit_safe": False,
        "attempt_count": 1,
        "last_attempt_at": "now()",
        "last_used_at": "now()",
    }
    try:
        result = db.table("scribe_v2_cache_entries").insert(payload).execute()
    except Exception:
        return None
    data = result.data
    return data[0] if isinstance(data, list) and data else None


def claim_failed_entry_for_retry(
    db,
    *,
    cache_key: str,
    owner_token: str,
    expected_attempt_count: int,
    require_resubmit_safe: bool = True,
) -> dict | None:
    """Claim a failed row only when another provider submission is explicitly safe.

    ``retryable`` alone is insufficient: a disconnected response may have left an
    accepted provider request running.  Requiring ``resubmit_safe`` prevents a
    second transcription (and its associated charge) in that case.
    """
    payload = {
        "status": "running",
        "owner_token": owner_token,
        "raw_json_r2_key": None,
        "raw_srt_r2_key": None,
        "external_task_id": None,
        "provider_request_id": None,
        "provider_transcription_id": None,
        "provider_trace_id": None,
        "error_message": None,
        "failure_kind": None,
        "retryable": False,
        "resubmit_safe": False,
        "completed_at": None,
        "attempt_count": max(0, int(expected_attempt_count)) + 1,
        "last_attempt_at": "now()",
        "last_used_at": "now()",
    }
    query = (
        db.table("scribe_v2_cache_entries")
        .update(payload)
        .eq("cache_key", cache_key)
        .eq("status", "failed")
        .eq("attempt_count", max(0, int(expected_attempt_count)))
    )
    if require_resubmit_safe:
        query = query.eq("retryable", True).eq("resubmit_safe", True)
    result = query.execute()
    data = getattr(result, "data", None)
    return data[0] if isinstance(data, list) and data else None


def record_provider_status(
    db,
    *,
    cache_key: str,
    owner_token: str,
    payload: dict[str, Any],
    expected_status: str = "running",
) -> bool:
    """Persist identifiers and recovery classification exposed by Chalna status."""
    update: dict[str, Any] = {"last_used_at": "now()"}
    external_task_id = payload.get("job_id") or payload.get("external_task_id")
    if isinstance(external_task_id, str) and external_task_id:
        update["external_task_id"] = external_task_id

    value_map = {
        "provider_request_id": "provider_request_id",
        "provider_transcription_id": "provider_transcription_id",
        "provider_trace_id": "provider_trace_id",
        "failure_kind": "failure_kind",
    }
    for source_key, destination_key in value_map.items():
        value = payload.get(source_key)
        if isinstance(value, str) and value:
            update[destination_key] = value

    for key in ("retryable", "resubmit_safe"):
        value = payload.get(key)
        if isinstance(value, bool):
            update[key] = value

    if len(update) == 1:
        return False
    result = (
        db.table("scribe_v2_cache_entries")
        .update(update)
        .eq("cache_key", cache_key)
        .eq("status", expected_status)
        .eq("owner_token", owner_token)
        .execute()
    )
    return _first_row(result) is not None


def mark_cache_completed(
    db,
    *,
    cache_key: str,
    owner_token: str,
    raw_json_key: str,
    raw_srt_key: str,
    external_task_id: str | None,
    provider_request_id: str | None = None,
    provider_transcription_id: str | None = None,
    provider_trace_id: str | None = None,
) -> bool:
    payload = _completed_cache_payload(
        raw_json_key=raw_json_key,
        raw_srt_key=raw_srt_key,
        external_task_id=external_task_id,
        provider_request_id=provider_request_id,
        provider_transcription_id=provider_transcription_id,
        provider_trace_id=provider_trace_id,
        attempt_count=None,
    )
    result = (
        db.table("scribe_v2_cache_entries")
        .update(payload)
        .eq("cache_key", cache_key)
        .eq("status", "running")
        .eq("owner_token", owner_token)
        .execute()
    )
    return _first_row(result) is not None


def recover_failed_cache_as_completed(
    db,
    *,
    cache_key: str,
    raw_json_key: str,
    raw_srt_key: str,
    external_task_id: str | None,
    provider_request_id: str | None = None,
    provider_transcription_id: str | None = None,
    provider_trace_id: str | None = None,
    attempt_count: int | None = None,
    expected_owner_token: str | None | object = _EXPECTED_VALUE_UNSET,
    expected_attempt_count: int | object = _EXPECTED_VALUE_UNSET,
) -> dict | None:
    """Conditionally publish verified recovery artifacts for a failed row only."""
    payload = _completed_cache_payload(
        raw_json_key=raw_json_key,
        raw_srt_key=raw_srt_key,
        external_task_id=external_task_id,
        provider_request_id=provider_request_id,
        provider_transcription_id=provider_transcription_id,
        provider_trace_id=provider_trace_id,
        attempt_count=attempt_count,
    )
    query = (
        db.table("scribe_v2_cache_entries")
        .update(payload)
        .eq("cache_key", cache_key)
        .eq("status", "failed")
    )
    if expected_owner_token is None:
        query = query.is_("owner_token", "null")
    elif expected_owner_token is not _EXPECTED_VALUE_UNSET:
        query = query.eq("owner_token", expected_owner_token)
    if expected_attempt_count is not _EXPECTED_VALUE_UNSET:
        query = query.eq("attempt_count", max(0, int(expected_attempt_count)))
    return _first_row(query.execute())


def _completed_cache_payload(
    *,
    raw_json_key: str,
    raw_srt_key: str,
    external_task_id: str | None,
    provider_request_id: str | None,
    provider_transcription_id: str | None,
    provider_trace_id: str | None,
    attempt_count: int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "completed",
        "raw_json_r2_key": raw_json_key,
        "raw_srt_r2_key": raw_srt_key,
        "external_task_id": external_task_id,
        "error_message": None,
        "failure_kind": None,
        "retryable": False,
        "resubmit_safe": False,
        "completed_at": "now()",
        "last_used_at": "now()",
    }
    optional_ids = {
        "provider_request_id": provider_request_id,
        "provider_transcription_id": provider_transcription_id,
        "provider_trace_id": provider_trace_id,
    }
    payload.update({key: value for key, value in optional_ids.items() if value})
    if attempt_count is not None:
        payload["attempt_count"] = max(0, int(attempt_count))
    return payload


def mark_cache_failed(
    db,
    *,
    cache_key: str,
    owner_token: str,
    error_message: str,
    failure_kind: str | None = None,
    retryable: bool | None = None,
    resubmit_safe: bool | None = None,
) -> bool:
    payload: dict[str, Any] = {
        "status": "failed",
        "error_message": error_message[:1000],
        "last_used_at": "now()",
    }
    if failure_kind:
        payload["failure_kind"] = failure_kind
    if isinstance(retryable, bool):
        payload["retryable"] = retryable
    if isinstance(resubmit_safe, bool):
        payload["resubmit_safe"] = resubmit_safe
    result = (
        db.table("scribe_v2_cache_entries")
        .update(payload)
        .eq("cache_key", cache_key)
        .eq("status", "running")
        .eq("owner_token", owner_token)
        .execute()
    )
    return _first_row(result) is not None


def mark_unowned_running_cache_failed(
    db,
    *,
    cache_key: str,
    error_message: str,
) -> bool:
    """CAS a pre-owner-token legacy running row to recovery-required."""
    result = (
        db.table("scribe_v2_cache_entries")
        .update({
            "status": "failed",
            "error_message": error_message[:1000],
            "failure_kind": "recovery_required",
            "retryable": True,
            "resubmit_safe": False,
            "last_used_at": "now()",
        })
        .eq("cache_key", cache_key)
        .eq("status", "running")
        .is_("owner_token", "null")
        .execute()
    )
    return _first_row(result) is not None


def _first_row(result) -> dict | None:
    data = getattr(result, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None


def record_cache_hit(db, entry: dict) -> None:
    db.table("scribe_v2_cache_entries").update({
        "hit_count": int(entry.get("hit_count") or 0) + 1,
        "last_used_at": "now()",
    }).eq("cache_key", entry["cache_key"]).execute()


def wait_for_running_entry(
    db,
    *,
    cache_key: str,
    timeout_seconds: float = RUNNING_POLL_TIMEOUT_SECONDS,
    interval_seconds: float = RUNNING_POLL_INTERVAL_SECONDS,
) -> dict:
    started = time.monotonic()
    generation: tuple[Any, int] | None = None
    while True:
        entry = get_cache_entry(db, cache_key)
        if not entry:
            raise RuntimeError("Scribe V2 cache row disappeared while waiting")
        if entry.get("status") != "running":
            return entry
        current_generation = (
            entry.get("owner_token"),
            int(entry.get("attempt_count") or 0),
        )
        if generation is None:
            generation = current_generation
        elif current_generation != generation:
            generation = current_generation
            started = time.monotonic()
        if time.monotonic() - started > timeout_seconds:
            owner_token = entry.get("owner_token")
            if isinstance(owner_token, str) and owner_token:
                transitioned = mark_cache_failed(
                    db,
                    cache_key=cache_key,
                    owner_token=owner_token,
                    error_message="Scribe V2 cache owner stopped reporting progress",
                    failure_kind="recovery_required",
                    retryable=True,
                    resubmit_safe=False,
                )
            else:
                transitioned = mark_unowned_running_cache_failed(
                    db,
                    cache_key=cache_key,
                    error_message="Scribe V2 cache owner stopped reporting progress",
                )
            if transitioned:
                raise RuntimeError(
                    "Scribe V2 cache generation timed out; accepted work requires recovery"
                )
            # The observed generation changed while the timeout CAS was in
            # flight. Follow the authoritative row instead of failing it.
            latest = get_cache_entry(db, cache_key)
            if not latest:
                raise RuntimeError("Scribe V2 cache row disappeared while recovering timeout")
            if latest.get("status") != "running":
                return latest
            generation = (
                latest.get("owner_token"),
                int(latest.get("attempt_count") or 0),
            )
            started = time.monotonic()
        time.sleep(interval_seconds)
