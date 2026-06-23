"""Global source file registry helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path


SOURCE_ASSET_SELECT = (
    "id, sha256, size_bytes, r2_key, filename, duration_seconds, "
    "derived_status, media_info_r2_key, audio_proxy_r2_key, audio_codec, "
    "sample_rate, channels, duration_ms, duration_diff_ms, media_info_version, "
    "derived_error"
)


def sha256_file(path: str | Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    """Return SHA-256 for the exact file bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lookup_source_asset(db, *, sha256: str, size_bytes: int) -> dict | None:
    result = (
        db.table("source_assets")
        .select(SOURCE_ASSET_SELECT)
        .eq("sha256", sha256)
        .eq("size_bytes", size_bytes)
        .limit(1)
        .execute()
    )
    data = getattr(result, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None


def lookup_source_asset_by_r2_key(db, *, r2_key: str) -> dict | None:
    result = (
        db.table("source_assets")
        .select(SOURCE_ASSET_SELECT)
        .eq("r2_key", r2_key)
        .limit(1)
        .execute()
    )
    data = getattr(result, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None


def touch_source_asset(db, *, asset_id: str) -> None:
    db.table("source_assets").update({"last_used_at": "now()"}).eq("id", asset_id).execute()


def delete_source_asset(db, *, asset_id: str) -> None:
    db.table("source_assets").delete().eq("id", asset_id).execute()


def upsert_source_asset(
    db,
    *,
    sha256: str,
    size_bytes: int,
    r2_key: str,
    filename: str | None,
    duration_seconds: int | None,
    derived: dict | None = None,
) -> dict | None:
    payload = {
        "sha256": sha256,
        "size_bytes": size_bytes,
        "r2_key": r2_key,
        "filename": filename,
        "duration_seconds": duration_seconds,
        "last_used_at": "now()",
    }
    if derived is not None:
        payload.update({
            "derived_status": derived.get("status"),
            "media_info_r2_key": derived.get("media_info_r2_key"),
            "audio_proxy_r2_key": derived.get("audio_proxy_r2_key"),
            "audio_codec": derived.get("audio_codec"),
            "sample_rate": derived.get("sample_rate"),
            "channels": derived.get("channels"),
            "duration_ms": derived.get("duration_ms"),
            "duration_diff_ms": derived.get("duration_diff_ms"),
            "media_info_version": derived.get("media_info_version"),
            "derived_error": derived.get("error"),
            "derived_at": "now()" if derived.get("status") == "ready" else None,
        })
    result = (
        db.table("source_assets")
        .upsert(payload, on_conflict="sha256,size_bytes")
        .execute()
    )
    data = result.data
    return data[0] if isinstance(data, list) and data else None
