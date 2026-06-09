"""Local cache helpers for final preview rendering and streaming."""

import hashlib
import json
import secrets
from pathlib import Path

from eogum.config import settings


def decision_hash(payload: dict) -> str:
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def preview_cache_key(project_id: str, hash_value: str) -> str:
    return f"{project_id}/{hash_value}"


def preview_cache_dir(project_id: str, hash_value: str) -> Path:
    return settings.final_preview_cache_dir / project_id / hash_value


def preview_cache_paths(project_id: str, hash_value: str) -> tuple[Path, Path]:
    directory = preview_cache_dir(project_id, hash_value)
    return directory / "preview.mp4", directory / "captions.vtt"


def preview_cache_ready(project_id: str, hash_value: str) -> bool:
    video_path, captions_path = preview_cache_paths(project_id, hash_value)
    return video_path.is_file() and video_path.stat().st_size > 0 and captions_path.is_file()


def new_cache_token() -> str:
    return secrets.token_urlsafe(32)


def source_cache_path(r2_key: str, suffix: str) -> Path:
    digest = hashlib.sha256(r2_key.encode("utf-8")).hexdigest()
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix}" if suffix else ".mp4"
    return settings.source_cache_dir / f"{digest}{safe_suffix}"
