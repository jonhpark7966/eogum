"""Overlapped speech detection used by optional overlap protection jobs."""

from __future__ import annotations

import os
import platform
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eogum.config import settings

DIARIZATION_MODEL_ID = "pyannote/speaker-diarization-community-1"


class OverlapProtectionError(RuntimeError):
    """Raised when overlap protection is enabled but no detector succeeds."""

    def __init__(self, message: str, payload: dict[str, Any]):
        super().__init__(message)
        self.payload = payload


def build_overlap_protection_artifact(
    source_path: str | Path,
    output_dir: str | Path,
) -> tuple[Path, dict[str, Any]]:
    """Run the community diarization overlap detector and write the artifact JSON."""
    source = Path(source_path)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    cache_dir = settings.huggingface_cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    wav_path = output_root / "source.overlap.16k_mono.wav"
    _extract_audio(source, wav_path)
    duration_ms = _ffprobe_duration_ms(wav_path)

    model_results: dict[str, dict[str, Any]] = {}
    model_intervals: list[dict[str, Any]] = []

    model_key = "community1"
    model_started = time.time()
    try:
        intervals = _run_community1_detector(wav_path, cache_dir)
        for interval in intervals:
            model_intervals.append({**interval, "models": [model_key]})
        model_results[model_key] = {
            "status": "succeeded",
            "model": DIARIZATION_MODEL_ID,
            "intervals": len(intervals),
            "total_overlap_ms": _total_ms(intervals),
            "elapsed_seconds": round(time.time() - model_started, 3),
        }
    except Exception as exc:
        model_results[model_key] = {
            "status": "failed",
            "model": DIARIZATION_MODEL_ID,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(limit=20),
            "elapsed_seconds": round(time.time() - model_started, 3),
        }

    status = "complete" if model_results[model_key].get("status") == "succeeded" else "failed"
    payload = {
        "schema_version": "overlap_protection/v1",
        "enabled": True,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(source),
        "audio_path": str(wav_path),
        "audio_duration_ms": duration_ms,
        "environment": _environment_payload(),
        "models": model_results,
        "intervals": _merge_intervals(model_intervals),
        "elapsed_seconds": round(time.time() - started, 3),
    }
    payload["interval_count"] = len(payload["intervals"])
    payload["total_overlap_ms"] = _total_ms(payload["intervals"])

    artifact_path = output_root / "overlap_protection.json"
    artifact_path.write_text(_json_dumps(payload), encoding="utf-8")

    if status == "failed":
        raise OverlapProtectionError("Overlap detector failed", payload)

    return artifact_path, payload


def _extract_audio(source: Path, wav_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(wav_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {(result.stderr or result.stdout)[-1000:]}")


def _run_community1_detector(wav_path: Path, cache_dir: Path) -> list[dict[str, Any]]:
    import torch
    from pyannote.audio import Pipeline

    pipeline = _from_pretrained(
        Pipeline,
        DIARIZATION_MODEL_ID,
        cache_dir=cache_dir,
    )
    if pipeline is None:
        raise RuntimeError(f"Pipeline.from_pretrained returned None for {DIARIZATION_MODEL_ID}")
    device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
    pipeline.to(device)
    output = pipeline(str(wav_path))
    annotation = getattr(output, "speaker_diarization", output)
    return _infer_overlaps_from_turns(_annotation_to_turns(annotation))


def _from_pretrained(factory: Any, model_id: str, *, cache_dir: Path, **kwargs: Any) -> Any:
    token = _hf_token()
    base_kwargs = {**kwargs, "cache_dir": str(cache_dir)}
    if token:
        base_kwargs["token"] = token
    try:
        return factory.from_pretrained(model_id, **base_kwargs)
    except TypeError:
        base_kwargs.pop("token", None)
        if token:
            base_kwargs["use_auth_token"] = token
        return factory.from_pretrained(model_id, **base_kwargs)


def _hf_token() -> str | None:
    return (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGINGFACE_HUB_TOKEN")
        or os.environ.get("HUGGINGFACE_TOKEN")
        or settings.hf_token
        or settings.huggingface_hub_token
        or None
    )


def _annotation_to_turns(annotation: Any) -> list[dict[str, Any]]:
    turns = []
    if hasattr(annotation, "itertracks"):
        for segment, _track, label in annotation.itertracks(yield_label=True):
            turns.append(_interval(segment.start, segment.end, speaker=str(label)))
    else:
        for item in annotation:
            if len(item) == 2:
                segment, label = item
                turns.append(_interval(segment.start, segment.end, speaker=str(label)))
    return sorted(turns, key=lambda item: (item["start_ms"], item["end_ms"], item.get("speaker") or ""))


def _infer_overlaps_from_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[tuple[int, int, int]] = []
    for index, turn in enumerate(turns):
        events.append((turn["start_ms"], 1, index))
        events.append((turn["end_ms"], -1, index))
    events.sort(key=lambda event: (event[0], event[1]))

    active: set[int] = set()
    previous_time: int | None = None
    overlaps: list[dict[str, Any]] = []
    for current_time, kind, index in events:
        if previous_time is not None and current_time > previous_time and len(active) >= 2:
            speakers = sorted({str(turns[i]["speaker"]) for i in active if turns[i].get("speaker")})
            overlaps.append(_interval(previous_time / 1000.0, current_time / 1000.0, speakers=speakers))
        if kind == -1:
            active.discard(index)
        else:
            active.add(index)
        previous_time = current_time
    return _merge_intervals(overlaps, require_same_models=False)


def _interval(start: float, end: float, **extra: Any) -> dict[str, Any]:
    start_ms = int(round(float(start) * 1000.0))
    end_ms = int(round(float(end) * 1000.0))
    result: dict[str, Any] = {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "start": round(start_ms / 1000.0, 3),
        "end": round(end_ms / 1000.0, 3),
        "duration_ms": max(0, end_ms - start_ms),
    }
    result.update(extra)
    return result


def _merge_intervals(
    intervals: list[dict[str, Any]],
    *,
    require_same_models: bool = False,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for interval in sorted(intervals, key=lambda item: (item["start_ms"], item["end_ms"])):
        if interval["end_ms"] <= interval["start_ms"]:
            continue
        models = set(interval.get("models") or [])
        if not merged:
            merged.append({**interval, "models": sorted(models)})
            continue
        previous = merged[-1]
        previous_models = set(previous.get("models") or [])
        same_models = previous_models == models if require_same_models else True
        if same_models and interval["start_ms"] <= previous["end_ms"]:
            previous["end_ms"] = max(previous["end_ms"], interval["end_ms"])
            previous["end"] = round(previous["end_ms"] / 1000.0, 3)
            previous["duration_ms"] = previous["end_ms"] - previous["start_ms"]
            previous["models"] = sorted(previous_models | models)
            if interval.get("speakers"):
                previous["speakers"] = sorted(set(previous.get("speakers") or []) | set(interval["speakers"]))
        else:
            merged.append({**interval, "models": sorted(models)})
    return merged


def _ffprobe_duration_ms(path: Path) -> int | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return None
    try:
        return int(round(float(result.stdout.strip()) * 1000.0))
    except (TypeError, ValueError):
        return None


def _total_ms(intervals: list[dict[str, Any]]) -> int:
    return sum(max(0, int(item["end_ms"]) - int(item["start_ms"])) for item in intervals)


def _environment_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "python": platform.python_version(),
    }
    try:
        import pyannote.audio

        payload["pyannote_audio"] = pyannote.audio.__version__
    except Exception:
        payload["pyannote_audio"] = None
    try:
        import torch

        payload["torch"] = torch.__version__
        payload["cuda_available"] = torch.cuda.is_available()
        payload["cuda_device"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except Exception:
        payload["torch"] = None
        payload["cuda_available"] = None
        payload["cuda_device"] = None
    return payload


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
