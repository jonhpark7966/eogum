"""Shared FFprobe and interval-based FFmpeg rendering."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Callable


FINAL_PREVIEW_PROFILE = "final_preview_v1"
WEB_1080P_PROFILE = "web_1080p_v2"
WEB_VIDEO_BITRATE_TOLERANCE_RATIO = 0.10


def _run(command: list[str], *, timeout: int, description: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"{description} failed: {result.stderr[-1000:]}")
    return result


def _fraction_value(value: object) -> float | None:
    if not isinstance(value, str) or not value or value == "0/0":
        return None
    try:
        numerator, separator, denominator = value.partition("/")
        return float(numerator) / float(denominator) if separator else float(numerator)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def probe_media(path: Path) -> dict:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        timeout=30,
        description=f"ffprobe {path}",
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if not video:
        raise RuntimeError(f"video stream not found in {path}")

    format_metadata = payload.get("format") or {}
    duration_value = format_metadata.get("duration") or video.get("duration")
    try:
        duration_ms = int(round(float(duration_value) * 1000))
    except (TypeError, ValueError):
        raise RuntimeError(f"ffprobe duration missing for {path}") from None

    video_duration = video.get("duration")
    audio_duration = audio.get("duration") if audio else None
    try:
        av_sync_diff_ms = (
            abs(int(round((float(video_duration) - float(audio_duration)) * 1000)))
            if video_duration is not None and audio_duration is not None
            else None
        )
    except (TypeError, ValueError):
        av_sync_diff_ms = None

    overall_bitrate = _positive_int(format_metadata.get("bit_rate"))
    audio_bitrate = _positive_int(audio.get("bit_rate")) if audio else None
    video_bitrate = _positive_int(video.get("bit_rate"))
    video_bitrate_estimated = False
    if video_bitrate is None:
        if overall_bitrate is None and duration_ms > 0:
            overall_bitrate = int(round(path.stat().st_size * 8 * 1000 / duration_ms))
        known_audio_bitrate = sum(
            _positive_int(stream.get("bit_rate")) or 0
            for stream in streams
            if stream.get("codec_type") == "audio"
        )
        if overall_bitrate is not None and overall_bitrate > known_audio_bitrate:
            video_bitrate = overall_bitrate - known_audio_bitrate
            video_bitrate_estimated = True

    return {
        "duration_ms": duration_ms,
        "size_bytes": path.stat().st_size,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_channels": int(audio.get("channels") or 0) if audio else None,
        "has_audio": audio is not None,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": _fraction_value(video.get("avg_frame_rate")) or _fraction_value(video.get("r_frame_rate")),
        "av_sync_diff_ms": av_sync_diff_ms,
        "overall_bitrate": overall_bitrate,
        "video_bitrate": video_bitrate,
        "video_bitrate_estimated": video_bitrate_estimated,
        "audio_bitrate": audio_bitrate,
    }


def probe_duration_ms(path: Path) -> int:
    return int(probe_media(path)["duration_ms"])


def has_audio_stream(path: Path) -> bool:
    return bool(probe_media(path)["has_audio"])


def _encoding_args(
    profile: str,
    *,
    has_audio: bool,
    target_video_bitrate: int | None = None,
) -> list[str]:
    if profile == FINAL_PREVIEW_PROFILE:
        args = [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
        ]
        if has_audio:
            args += ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]
        else:
            args += ["-an"]
        return args

    if profile == WEB_1080P_PROFILE:
        target_video_bitrate = _positive_int(target_video_bitrate)
        if target_video_bitrate is None:
            raise RuntimeError("source video bitrate is unavailable for web render")
        args = [
            "-vf",
            "scale=min(1920\\,iw):min(1080\\,ih):force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-b:v",
            str(target_video_bitrate),
            "-minrate",
            str(target_video_bitrate),
            "-maxrate",
            str(target_video_bitrate),
            "-bufsize",
            str(target_video_bitrate * 2),
            "-x264-params",
            "nal-hrd=cbr:force-cfr=0",
            "-pix_fmt",
            "yuv420p",
        ]
        if has_audio:
            args += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
        else:
            args += ["-an"]
        return args

    raise ValueError(f"Unsupported render profile: {profile}")


def validate_output(
    output_path: Path,
    *,
    profile: str,
    expected_duration_ms: int | None = None,
    interval_count: int = 1,
    expected_video_bitrate: int | None = None,
) -> dict:
    metadata = probe_media(output_path)
    if metadata["size_bytes"] <= 0:
        raise RuntimeError("rendered output is empty")
    if profile == WEB_1080P_PROFILE:
        if metadata["video_codec"] != "h264":
            raise RuntimeError(f"unexpected video codec: {metadata['video_codec']}")
        if metadata["audio_codec"] not in {None, "aac"}:
            raise RuntimeError(f"unexpected audio codec: {metadata['audio_codec']}")
        if metadata["audio_codec"] == "aac" and metadata["audio_channels"] != 2:
            raise RuntimeError(f"unexpected audio channel count: {metadata['audio_channels']}")
        if metadata["av_sync_diff_ms"] is not None and metadata["av_sync_diff_ms"] > 1000:
            raise RuntimeError(f"rendered A/V sync drift is too large: {metadata['av_sync_diff_ms']}ms")
        if metadata["width"] > 1920 or metadata["height"] > 1080:
            raise RuntimeError(
                f"rendered dimensions exceed 1080p profile: {metadata['width']}x{metadata['height']}"
            )
        if expected_video_bitrate is not None:
            actual_video_bitrate = metadata.get("video_bitrate")
            if actual_video_bitrate is None:
                raise RuntimeError("rendered video bitrate is unavailable")
            bitrate_delta_ratio = abs(actual_video_bitrate - expected_video_bitrate) / expected_video_bitrate
            if bitrate_delta_ratio > WEB_VIDEO_BITRATE_TOLERANCE_RATIO:
                raise RuntimeError(
                    "rendered video bitrate differs from source target: "
                    f"expected={expected_video_bitrate}bps actual={actual_video_bitrate}bps"
                )
            metadata["target_video_bitrate"] = expected_video_bitrate
            metadata["video_bitrate_delta_percent"] = (
                (actual_video_bitrate - expected_video_bitrate) / expected_video_bitrate * 100
            )
    if expected_duration_ms is not None:
        tolerance_ms = max(500, interval_count * 100)
        if abs(metadata["duration_ms"] - expected_duration_ms) > tolerance_ms:
            raise RuntimeError(
                "rendered duration differs from interval plan: "
                f"expected={expected_duration_ms}ms actual={metadata['duration_ms']}ms"
            )
    return metadata


def render_intervals(
    source_path: Path,
    intervals: list[tuple[float, float]],
    output_path: Path,
    *,
    profile: str,
    progress_callback: Callable[[float], None] | None = None,
) -> dict:
    """Encode keep intervals independently, then stream-copy concatenate them."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    segment_dir = output_path.parent / f"{output_path.stem}_segments"
    segment_dir.mkdir(parents=True, exist_ok=True)
    source_metadata = probe_media(source_path)
    has_audio = bool(source_metadata["has_audio"])
    target_video_bitrate = (
        source_metadata.get("video_bitrate") if profile == WEB_1080P_PROFILE else None
    )
    valid_intervals = [(start, duration) for start, duration in intervals if duration > 0]

    segment_paths: list[Path] = []
    manifest_intervals: list[dict] = []
    preview_cursor_ms = 0
    for index, (start, duration) in enumerate(valid_intervals):
        segment_path = segment_dir / f"segment_{index:04d}.mp4"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-ss",
            f"{max(0.0, start):.6f}",
            "-i",
            str(source_path),
            "-t",
            f"{duration:.6f}",
            "-map",
            "0:v:0",
        ]
        if has_audio:
            command += ["-map", "0:a:0"]
        command += _encoding_args(
            profile,
            has_audio=has_audio,
            target_video_bitrate=target_video_bitrate,
        )
        command += ["-movflags", "+faststart", str(segment_path)]
        _run(
            command,
            timeout=7200,
            description=(
                f"render interval {index + 1}/{len(valid_intervals)} "
                f"(start={start:.3f}s, duration={duration:.3f}s)"
            ),
        )

        actual_duration_ms = probe_duration_ms(segment_path)
        source_start_ms = int(round(start * 1000))
        requested_duration_ms = int(round(duration * 1000))
        manifest_intervals.append({
            "source_start_ms": source_start_ms,
            "source_end_ms": source_start_ms + requested_duration_ms,
            "requested_duration_ms": requested_duration_ms,
            "actual_duration_ms": actual_duration_ms,
            "preview_start_ms": preview_cursor_ms,
            "preview_end_ms": preview_cursor_ms + actual_duration_ms,
        })
        preview_cursor_ms += actual_duration_ms
        segment_paths.append(segment_path)
        if progress_callback:
            progress_callback((index + 1) / len(valid_intervals))

    if not segment_paths:
        raise RuntimeError("렌더링할 keep 구간이 없습니다")

    concat_list = output_path.with_suffix(".concat.txt")
    concat_list.write_text(
        "\n".join(f"file '{str(path).replace(chr(39), chr(92) + chr(39))}'" for path in segment_paths),
        encoding="utf-8",
    )
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        timeout=7200,
        description="render concat",
    )
    return {
        "version": 1,
        "intervals": manifest_intervals,
        "source": source_metadata,
        "target_video_bitrate": target_video_bitrate,
    }
