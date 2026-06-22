"""Database and key helpers for the global Scribe V2 raw cache."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass


SCRIBE_API_VERSION = "scribe_v2"
RUNNING_POLL_INTERVAL_SECONDS = 5.0
RUNNING_POLL_TIMEOUT_SECONDS = 2 * 60 * 60.0


@dataclass(frozen=True)
class ScribeV2CacheParams:
    source_sha256: str
    source_size_bytes: int
    language: str
    diarize: bool
    num_speakers: int | None
    tag_audio_events: bool
    scribe_api_version: str = SCRIBE_API_VERSION


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


def create_running_entry(db, *, cache_key: str, params: ScribeV2CacheParams) -> dict | None:
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
        "error_message": None,
        "last_used_at": "now()",
    }
    try:
        result = db.table("scribe_v2_cache_entries").insert(payload).execute()
    except Exception:
        return None
    data = result.data
    return data[0] if isinstance(data, list) and data else None


def mark_cache_completed(
    db,
    *,
    cache_key: str,
    raw_json_key: str,
    raw_srt_key: str,
    external_task_id: str | None,
) -> None:
    db.table("scribe_v2_cache_entries").update({
        "status": "completed",
        "raw_json_r2_key": raw_json_key,
        "raw_srt_r2_key": raw_srt_key,
        "external_task_id": external_task_id,
        "error_message": None,
        "completed_at": "now()",
        "last_used_at": "now()",
    }).eq("cache_key", cache_key).execute()


def mark_cache_failed(db, *, cache_key: str, error_message: str) -> None:
    db.table("scribe_v2_cache_entries").update({
        "status": "failed",
        "error_message": error_message[:1000],
        "last_used_at": "now()",
    }).eq("cache_key", cache_key).execute()


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
    while True:
        entry = get_cache_entry(db, cache_key)
        if not entry:
            raise RuntimeError("Scribe V2 cache row disappeared while waiting")
        if entry.get("status") != "running":
            return entry
        if time.monotonic() - started > timeout_seconds:
            raise RuntimeError("Scribe V2 cache generation timed out")
        time.sleep(interval_seconds)
