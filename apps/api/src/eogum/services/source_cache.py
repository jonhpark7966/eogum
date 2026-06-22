"""Global source file registry helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path


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
        .select("id, sha256, size_bytes, r2_key, filename, duration_seconds")
        .eq("sha256", sha256)
        .eq("size_bytes", size_bytes)
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
) -> dict | None:
    payload = {
        "sha256": sha256,
        "size_bytes": size_bytes,
        "r2_key": r2_key,
        "filename": filename,
        "duration_seconds": duration_seconds,
        "last_used_at": "now()",
    }
    result = (
        db.table("source_assets")
        .upsert(payload, on_conflict="sha256,size_bytes")
        .execute()
    )
    data = result.data
    return data[0] if isinstance(data, list) and data else None
