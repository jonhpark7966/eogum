"""Derived media assets for audio-based multicam sync."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any

from eogum.services import r2, source_cache

logger = logging.getLogger(__name__)

AUDIO_PROXY_SAMPLE_RATE = 16000
AUDIO_PROXY_CHANNELS = 1
AUDIO_PROXY_CODEC = "flac"
READY_STATUS = "ready"
MEDIA_INFO_SCHEMA_VERSION = 2


def is_ready(derived: dict | None) -> bool:
    return bool(
        isinstance(derived, dict)
        and derived.get("status") == READY_STATUS
        and derived.get("media_info_version") == MEDIA_INFO_SCHEMA_VERSION
        and derived.get("media_info_r2_key")
        and derived.get("audio_proxy_r2_key")
    )


def queued_snapshot() -> dict[str, Any]:
    return {
        "status": "queued",
        "media_info_r2_key": None,
        "audio_proxy_r2_key": None,
        "audio_codec": AUDIO_PROXY_CODEC,
        "sample_rate": AUDIO_PROXY_SAMPLE_RATE,
        "channels": AUDIO_PROXY_CHANNELS,
        "duration_ms": None,
        "duration_diff_ms": None,
        "media_info_version": MEDIA_INFO_SCHEMA_VERSION,
        "error": None,
    }


def processing_snapshot() -> dict[str, Any]:
    snapshot = queued_snapshot()
    snapshot["status"] = "processing"
    return snapshot


def failed_snapshot(error: str) -> dict[str, Any]:
    snapshot = queued_snapshot()
    snapshot["status"] = "failed"
    snapshot["error"] = error[:1000]
    return snapshot


def derived_r2_keys(source_r2_key: str) -> tuple[str, str]:
    source_path = Path(source_r2_key)
    stem = str(source_path.with_suffix(""))
    prefix = f"derived/{stem}"
    return f"{prefix}/media_info.json", f"{prefix}/audio_proxy.flac"


def source_file_hint(filename: str | None) -> str:
    name = (filename or "source").strip() or "source"
    return str(Path("/tmp/eogum/media") / name)


def normalize_asset_row(row: dict | None) -> dict:
    if not row:
        return {}
    return {
        "status": row.get("derived_status"),
        "media_info_r2_key": row.get("media_info_r2_key"),
        "audio_proxy_r2_key": row.get("audio_proxy_r2_key"),
        "audio_codec": row.get("audio_codec"),
        "sample_rate": row.get("sample_rate"),
        "channels": row.get("channels"),
        "duration_ms": row.get("duration_ms"),
        "duration_diff_ms": row.get("duration_diff_ms"),
        "media_info_version": row.get("media_info_version"),
        "error": row.get("derived_error"),
    }


def source_keys_needing_derivatives(project: dict, *, force: bool = False) -> list[str]:
    keys: list[str] = []
    if force or not is_ready(project.get("source_derived") or {}):
        keys.append("primary")
    for index, source in enumerate(project.get("extra_sources") or []):
        if force or not is_ready((source or {}).get("derived") or {}):
            keys.append(f"extra:{index}")
    return keys


def set_project_source_snapshot(project: dict, source_key: str, snapshot: dict, source_sha256: str | None = None) -> dict:
    updated = dict(project)
    if source_key == "primary":
        updated["source_derived"] = snapshot
        if source_sha256:
            updated["source_sha256"] = source_sha256
        return updated

    prefix, _, index_text = source_key.partition(":")
    if prefix != "extra" or not index_text.isdigit():
        raise ValueError(f"Unknown source_key: {source_key}")

    index = int(index_text)
    extra_sources = [dict(item or {}) for item in (updated.get("extra_sources") or [])]
    if index >= len(extra_sources):
        raise ValueError(f"Unknown extra source index: {source_key}")
    extra_sources[index]["derived"] = snapshot
    if source_sha256:
        extra_sources[index]["source_sha256"] = source_sha256
    updated["extra_sources"] = extra_sources
    return updated


def source_ref(project: dict, source_key: str) -> dict:
    if source_key == "primary":
        return {
            "source_key": "primary",
            "r2_key": project.get("source_r2_key"),
            "filename": project.get("source_filename"),
            "size_bytes": project.get("source_size_bytes"),
            "sha256": project.get("source_sha256"),
            "derived": project.get("source_derived") or {},
        }

    prefix, _, index_text = source_key.partition(":")
    if prefix != "extra" or not index_text.isdigit():
        raise ValueError(f"Unknown source_key: {source_key}")
    index = int(index_text)
    extra_sources = project.get("extra_sources") or []
    if index >= len(extra_sources):
        raise ValueError(f"Unknown extra source index: {source_key}")
    source = extra_sources[index] or {}
    return {
        "source_key": source_key,
        "r2_key": source.get("r2_key"),
        "filename": source.get("filename"),
        "size_bytes": source.get("size_bytes"),
        "sha256": source.get("source_sha256"),
        "derived": source.get("derived") or {},
    }


def build_manifest(project: dict, output_path: Path, local_sources: dict[str, dict[str, str]]) -> Path:
    primary_ref = source_ref(project, "primary")
    extras = []
    for index, extra in enumerate(project.get("extra_sources") or []):
        key = f"extra:{index}"
        local = local_sources[key]
        extras.append({
            "source_key": key,
            "original_name": extra.get("filename"),
            "path_hint": source_file_hint(extra.get("filename")),
            "media_info_path": local["media_info_path"],
            "audio_proxy_path": local["audio_proxy_path"],
            "offset_ms": extra.get("offset_ms"),
        })

    local_primary = local_sources["primary"]
    manifest = {
        "schema_version": "avid.multicam_sources.v1",
        "primary": {
            "source_key": "primary",
            "original_name": primary_ref.get("filename"),
            "path_hint": source_file_hint(primary_ref.get("filename")),
            "media_info_path": local_primary["media_info_path"],
            "audio_proxy_path": local_primary["audio_proxy_path"],
        },
        "extras": extras,
    }
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def download_ready_derivatives(project: dict, temp_dir: Path) -> dict[str, dict[str, str]]:
    local_sources: dict[str, dict[str, str]] = {}
    keys = ["primary", *[f"extra:{i}" for i, _ in enumerate(project.get("extra_sources") or [])]]
    for source_key in keys:
        ref = source_ref(project, source_key)
        derived = ref.get("derived") or {}
        if not is_ready(derived):
            raise RuntimeError(f"{source_key} derived asset is not ready")
        source_dir = temp_dir / source_key.replace(":", "_")
        source_dir.mkdir(parents=True, exist_ok=True)
        media_info_path = source_dir / "media_info.json"
        audio_proxy_path = source_dir / "audio_proxy.flac"
        r2.download_file(derived["media_info_r2_key"], str(media_info_path))
        r2.download_file(derived["audio_proxy_r2_key"], str(audio_proxy_path))
        local_sources[source_key] = {
            "media_info_path": str(media_info_path),
            "audio_proxy_path": str(audio_proxy_path),
        }
    return local_sources


def derive_r2_source(ref: dict, temp_root: Path) -> tuple[dict, str]:
    r2_key = ref.get("r2_key")
    filename = ref.get("filename")
    if not r2_key:
        raise RuntimeError(f"{ref.get('source_key')} source is missing r2_key")

    suffix = Path(filename or r2_key).suffix
    with tempfile.TemporaryDirectory(prefix="source_derivative_", dir=str(temp_root)) as tmp:
        work_dir = Path(tmp)
        source_path = work_dir / f"source{suffix}"
        r2.download_file(r2_key, str(source_path))
        return derive_local_source(
            source_path=source_path,
            source_key=ref.get("source_key") or "source",
            source_r2_key=r2_key,
            filename=filename,
            size_bytes=ref.get("size_bytes"),
        )


def derive_local_source(
    *,
    source_path: Path,
    source_key: str,
    source_r2_key: str,
    filename: str | None,
    size_bytes: int | None,
) -> tuple[dict, str]:
    source_path = Path(source_path)
    source_sha256 = source_cache.sha256_file(source_path)
    resolved_size = int(size_bytes or source_path.stat().st_size)

    ffprobe_payload = _ffprobe(source_path)
    media_info = _normalize_media_info(ffprobe_payload)

    work_dir = source_path.parent
    media_info_path = work_dir / "media_info.json"
    audio_proxy_path = work_dir / "audio_proxy.flac"
    media_info_doc = {
        "schema_version": MEDIA_INFO_SCHEMA_VERSION,
        "source_key": source_key,
        "source_r2_key": source_r2_key,
        "filename": filename,
        "size_bytes": resolved_size,
        "media_info": media_info,
        "ffprobe": ffprobe_payload,
    }
    media_info_path.write_text(
        json.dumps(media_info_doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _extract_audio_proxy(source_path, audio_proxy_path)
    audio_probe = _probe_audio_proxy(audio_proxy_path)
    duration_diff_ms = None
    if (
        media_info.get("duration_ms") is not None
        and audio_probe.get("duration_ms") is not None
    ):
        duration_diff_ms = abs(int(audio_probe["duration_ms"]) - int(media_info["duration_ms"]))

    media_info_r2_key, audio_proxy_r2_key = derived_r2_keys(source_r2_key)
    r2.upload_file(str(media_info_path), media_info_r2_key, "application/json")
    r2.upload_file(str(audio_proxy_path), audio_proxy_r2_key, "audio/flac")

    snapshot = {
        "status": READY_STATUS,
        "media_info_r2_key": media_info_r2_key,
        "audio_proxy_r2_key": audio_proxy_r2_key,
        "audio_codec": audio_probe.get("codec_name") or AUDIO_PROXY_CODEC,
        "sample_rate": audio_probe.get("sample_rate"),
        "channels": audio_probe.get("channels"),
        "duration_ms": audio_probe.get("duration_ms"),
        "duration_diff_ms": duration_diff_ms,
        "media_info_version": MEDIA_INFO_SCHEMA_VERSION,
        "error": None,
    }
    return snapshot, source_sha256


def persist_asset_derivative(
    db,
    *,
    source_sha256: str,
    size_bytes: int,
    r2_key: str,
    filename: str | None,
    duration_seconds: int | None,
    snapshot: dict,
) -> None:
    source_cache.upsert_source_asset(
        db,
        sha256=source_sha256,
        size_bytes=size_bytes,
        r2_key=r2_key,
        filename=filename,
        duration_seconds=duration_seconds,
        derived=snapshot,
    )


def _ffprobe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[-500:]}")
    return json.loads(result.stdout)


def _extract_audio_proxy(input_path: Path, output_path: Path) -> None:
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i", str(input_path),
            "-map", "0:a:0",
            "-vn",
            "-ac", str(AUDIO_PROXY_CHANNELS),
            "-ar", str(AUDIO_PROXY_SAMPLE_RATE),
            "-c:a", AUDIO_PROXY_CODEC,
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio proxy failed: {result.stderr[-500:]}")


def _probe_audio_proxy(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe audio proxy failed: {result.stderr[-500:]}")
    payload = json.loads(result.stdout)
    stream = next((item for item in payload.get("streams", []) if item.get("codec_type") == "audio"), {})
    duration = stream.get("duration") or payload.get("format", {}).get("duration")
    return {
        "codec_name": stream.get("codec_name"),
        "sample_rate": _int_or_none(stream.get("sample_rate")),
        "channels": _int_or_none(stream.get("channels")),
        "duration_ms": _duration_ms(duration),
    }


def _normalize_media_info(payload: dict) -> dict[str, Any]:
    format_info = payload.get("format") or {}
    streams = payload.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    fps_fraction = _rate_to_fraction(video_stream.get("avg_frame_rate")) or _rate_to_fraction(
        video_stream.get("r_frame_rate")
    )
    timecode_rate_fraction = (
        _rate_to_fraction(video_stream.get("r_frame_rate")) or fps_fraction
    )
    fps = float(fps_fraction) if fps_fraction else None
    video_duration = _duration_fraction(video_stream)
    format_duration_ms = _duration_ms(format_info.get("duration")) or 0
    video_duration_ms = int(video_duration * 1000) if video_duration else None
    video_frame_count = _int_or_none(video_stream.get("nb_frames"))
    estimated_frame_count = False
    if video_frame_count is None and video_duration and fps_fraction:
        video_frame_count = round(video_duration * fps_fraction)
        estimated_frame_count = True

    sample_rates = {
        rate
        for rate in (_int_or_none(stream.get("sample_rate")) for stream in audio_streams)
        if rate is not None
    }
    sample_rate = next(iter(sample_rates)) if len(sample_rates) == 1 else None
    audio_sample_count = None
    for stream in audio_streams:
        audio_sample_count = _int_or_none(stream.get("duration_ts"))
        if audio_sample_count is not None:
            break
    timecode = _extract_timecode(payload)
    parsed_timecode = (
        _parse_timecode_start(timecode, timecode_rate_fraction)
        if timecode and timecode_rate_fraction else None
    )

    return {
        "duration_ms": video_duration_ms or format_duration_ms,
        "width": _int_or_none(video_stream.get("width")),
        "height": _int_or_none(video_stream.get("height")),
        "fps": fps,
        "frame_duration": (
            f"{fps_fraction.denominator}/{fps_fraction.numerator}"
            if fps_fraction else None
        ),
        "video_frame_count": video_frame_count,
        "video_frame_count_is_estimated": estimated_frame_count,
        "video_duration": (
            f"{video_duration.numerator}/{video_duration.denominator}"
            if video_duration else None
        ),
        "sample_rate": sample_rate,
        "audio_channels": (
            sum(_int_or_none(stream.get("channels")) or 0 for stream in audio_streams)
            or None
        ),
        "audio_sources": len(audio_streams) or None,
        "audio_sample_rate": sample_rate,
        "audio_sample_count": audio_sample_count,
        "start_time": format_info.get("start_time") or video_stream.get("start_time"),
        "time_base": video_stream.get("time_base"),
        "timecode": timecode,
        "timecode_rate": (
            f"{timecode_rate_fraction.numerator}/{timecode_rate_fraction.denominator}"
            if timecode and timecode_rate_fraction else None
        ),
        "timecode_start_frames": parsed_timecode[0] if parsed_timecode else None,
        "timecode_start_seconds": parsed_timecode[1] if parsed_timecode else None,
    }


def _int_or_none(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _duration_ms(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None
    return int(parsed * 1000) if parsed > 0 else None


def _rate_to_fraction(value: object) -> Fraction | None:
    if not isinstance(value, str) or not value or "/" not in value:
        return None
    try:
        num, den = value.split("/", 1)
        fraction = Fraction(int(num), int(den))
    except (ValueError, ZeroDivisionError):
        return None
    return fraction if fraction > 0 else None


def _stream_timecode(stream: dict) -> str | None:
    tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
    value = tags.get("timecode") or tags.get("TIMECODE")
    return str(value).strip() if value else None


def _extract_timecode(payload: dict) -> str | None:
    streams = payload.get("streams") or []
    for stream in streams:
        if stream.get("codec_type") == "video":
            value = _stream_timecode(stream)
            if value:
                return value
    for stream in streams:
        if stream.get("codec_type") == "data":
            value = _stream_timecode(stream)
            if value:
                return value
    format_tags = payload.get("format", {}).get("tags")
    if isinstance(format_tags, dict):
        value = format_tags.get("timecode") or format_tags.get("TIMECODE")
        if value:
            return str(value).strip()
    return None


def _parse_timecode_start(timecode: str, rate: Fraction) -> tuple[int, str] | None:
    match = re.match(r"^(\d+):(\d{2}):(\d{2})[:;](\d{2})$", timecode.strip())
    if not match or rate <= 0:
        return None
    hours, minutes, seconds, frames = (int(part) for part in match.groups())
    nominal_fps = int(round(float(rate)))
    if nominal_fps <= 0 or frames >= nominal_fps:
        return None
    total_frames = ((hours * 3600 + minutes * 60 + seconds) * nominal_fps) + frames
    start_units = total_frames * rate.denominator
    return total_frames, f"{start_units}/{rate.numerator}"


def _duration_fraction(stream: dict) -> Fraction | None:
    duration_ts = _int_or_none(stream.get("duration_ts"))
    time_base = _rate_to_fraction(stream.get("time_base"))
    if duration_ts is not None and time_base:
        duration = duration_ts * time_base
        if duration > 0:
            return duration
    try:
        duration = Fraction(str(stream.get("duration")))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return duration if duration > 0 else None
