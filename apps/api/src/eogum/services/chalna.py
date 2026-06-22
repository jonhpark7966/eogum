"""Direct Chalna API client used when Eogum needs live transcription stages."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from eogum.config import settings


logger = logging.getLogger(__name__)

StatusCallback = Callable[[dict[str, Any]], None]


class ChalnaClientError(RuntimeError):
    """Raised when the Chalna API fails or returns an unusable response."""


@dataclass(frozen=True)
class RawScribeResult:
    raw_json_path: str
    raw_srt_path: str
    external_task_id: str


@dataclass(frozen=True)
class TranscriptionSrtResult:
    srt_path: str
    external_task_id: str
    metadata: dict[str, Any]
    segmentation_log: list[dict[str, Any]]
    processing_metadata: dict[str, Any]


def transcribe_to_srt(
    source_path: str,
    *,
    language: str = "ko",
    output_dir: str | None = None,
    context: str | None = None,
    diarize: bool = True,
    tag_audio_events: bool = True,
    num_speakers: int | None = None,
    use_llm_segmentation: bool = True,
    use_llm_refinement: bool = True,
    bypass_llm_segmentation_cache: bool = False,
    on_status: StatusCallback | None = None,
    llm_log_path: str | None = None,
    timeout_seconds: float = 7200.0,
    poll_interval_seconds: float = 1.0,
) -> str:
    """Transcribe a media file through Chalna async API and write the final SRT.

    The caller can persist live stage information by passing ``on_status``.
    """
    source = Path(source_path)
    if not source.exists():
        raise ChalnaClientError(f"Source file not found: {source}")

    output_root = Path(output_dir) if output_dir else source.parent
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / "source.srt"

    base_url = settings.chalna_url.rstrip("/")
    form_data = {
        "language": language,
        "use_alignment": "false",
        "use_llm_segmentation": str(use_llm_segmentation).lower(),
        "use_llm_refinement": str(use_llm_refinement).lower(),
        "bypass_llm_segmentation_cache": str(bypass_llm_segmentation_cache).lower(),
        "diarize": str(diarize).lower(),
        "tag_audio_events": str(tag_audio_events).lower(),
        "output_format": "json",
        "include_logs": "true",
        "include_intermediate": "false",
        **({"context": context} if context else {}),
    }
    if num_speakers is not None:
        form_data["num_speakers"] = str(num_speakers)

    with httpx.Client(timeout=60.0) as client:
        with source.open("rb") as file_obj:
            response = client.post(
                f"{base_url}/transcribe/async",
                files={"file": (source.name, file_obj, "application/octet-stream")},
                data=form_data,
            )

    if response.status_code != 200:
        raise ChalnaClientError(f"Chalna submit failed: {response.text[:500]}")

    submitted = response.json()
    job_id = submitted.get("job_id") or submitted.get("task_id")
    if not job_id:
        raise ChalnaClientError(f"Chalna submit response missing job_id: {submitted}")

    if on_status:
        on_status({"job_id": job_id, "status": submitted.get("status", "queued")})

    started = time.monotonic()
    with httpx.Client(timeout=60.0) as client:
        while True:
            if time.monotonic() - started > timeout_seconds:
                raise ChalnaClientError(f"Chalna transcription timed out after {timeout_seconds:.0f}s")

            status_response = client.get(f"{base_url}/jobs/{job_id}")
            if status_response.status_code != 200:
                raise ChalnaClientError(f"Chalna status failed: {status_response.text[:500]}")

            data = status_response.json()
            if on_status:
                on_status(data)

            status = data.get("status")
            if status == "completed":
                result_data = _coerce_result(data.get("result"))
                _append_chalna_llm_io_logs(
                    llm_log_path,
                    result_data,
                    task_id=str(job_id),
                    endpoint="/transcribe/async",
                )
                output_path.write_text(_segments_to_srt(result_data.get("segments") or []), encoding="utf-8")
                return str(output_path)

            if status == "failed":
                raise ChalnaClientError(data.get("error") or "Chalna transcription failed")

            time.sleep(poll_interval_seconds)


def transcribe_raw_scribe_to_files(
    source_path: str,
    *,
    language: str = "ko",
    output_dir: str | None = None,
    diarize: bool = True,
    tag_audio_events: bool = True,
    num_speakers: int | None = None,
    on_status: StatusCallback | None = None,
    timeout_seconds: float = 7200.0,
    poll_interval_seconds: float = 1.0,
) -> RawScribeResult:
    """Run only raw Scribe transcription and persist raw JSON/SRT locally."""
    source = Path(source_path)
    if not source.exists():
        raise ChalnaClientError(f"Source file not found: {source}")

    output_root = Path(output_dir) if output_dir else source.parent
    output_root.mkdir(parents=True, exist_ok=True)
    raw_json_path = output_root / "source.scribe.raw.json"
    raw_srt_path = output_root / "source.scribe.raw.srt"

    result_data, task_id = _submit_and_poll(
        endpoint="/transcribe/async",
        source=source,
        data={
            "language": language,
            "use_alignment": "false",
            "use_llm_segmentation": "false",
            "use_llm_refinement": "false",
            "diarize": str(diarize).lower(),
            "tag_audio_events": str(tag_audio_events).lower(),
            "output_format": "json",
            "include_logs": "false",
            "include_intermediate": "true",
            **({"num_speakers": str(num_speakers)} if num_speakers is not None else {}),
        },
        on_status=on_status,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )

    scribe_response = result_data.get("scribe_response")
    if not isinstance(scribe_response, dict):
        raise ChalnaClientError("Chalna raw transcription completed without Scribe raw JSON")

    raw_srt = result_data.get("raw_srt")
    if not isinstance(raw_srt, str) or not raw_srt.strip():
        raw_srt = _segments_to_srt(result_data.get("segments") or [])

    raw_json_path.write_text(
        json.dumps(scribe_response, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    raw_srt_path.write_text(raw_srt, encoding="utf-8")
    return RawScribeResult(
        raw_json_path=str(raw_json_path),
        raw_srt_path=str(raw_srt_path),
        external_task_id=task_id,
    )


def transcribe_from_scribe_response_to_srt(
    source_path: str,
    raw_json_path: str,
    *,
    language: str = "ko",
    output_dir: str | None = None,
    context: str | None = None,
    diarize: bool = True,
    tag_audio_events: bool = True,
    num_speakers: int | None = None,
    use_llm_segmentation: bool = True,
    use_llm_refinement: bool = True,
    bypass_llm_segmentation_cache: bool = False,
    on_status: StatusCallback | None = None,
    llm_log_path: str | None = None,
    timeout_seconds: float = 7200.0,
    poll_interval_seconds: float = 1.0,
) -> TranscriptionSrtResult:
    """Run segmentation/refinement from cached raw Scribe JSON and write final SRT."""
    source = Path(source_path)
    raw_json = Path(raw_json_path)
    if not source.exists():
        raise ChalnaClientError(f"Source file not found: {source}")
    if not raw_json.exists():
        raise ChalnaClientError(f"Scribe raw JSON not found: {raw_json}")

    output_root = Path(output_dir) if output_dir else source.parent
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / "source.srt"

    result_data, _task_id = _submit_and_poll(
        endpoint="/transcribe/from-scribe/async",
        source=source,
        raw_json=raw_json,
        data={
            "language": language,
            "use_alignment": "false",
            "use_llm_segmentation": str(use_llm_segmentation).lower(),
            "use_llm_refinement": str(use_llm_refinement).lower(),
            "bypass_llm_segmentation_cache": str(bypass_llm_segmentation_cache).lower(),
            "diarize": str(diarize).lower(),
            "tag_audio_events": str(tag_audio_events).lower(),
            "output_format": "json",
            "include_logs": "true",
            "include_intermediate": "false",
            **({"context": context} if context else {}),
            **({"num_speakers": str(num_speakers)} if num_speakers is not None else {}),
        },
        on_status=on_status,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    _append_chalna_llm_io_logs(
        llm_log_path,
        result_data,
        task_id=_task_id,
        endpoint="/transcribe/from-scribe/async",
    )
    output_path.write_text(_segments_to_srt(result_data.get("segments") or []), encoding="utf-8")
    metadata = result_data.get("metadata") if isinstance(result_data.get("metadata"), dict) else {}
    segmentation_log_value = result_data.get("segmentation_log")
    segmentation_log = [item for item in segmentation_log_value if isinstance(item, dict)] if isinstance(segmentation_log_value, list) else []
    return TranscriptionSrtResult(
        srt_path=str(output_path),
        external_task_id=_task_id,
        metadata=metadata,
        segmentation_log=segmentation_log,
        processing_metadata=summarize_segmentation_metadata(
            metadata=metadata,
            segmentation_log=segmentation_log,
            use_llm_segmentation=use_llm_segmentation,
            bypass_llm_segmentation_cache=bypass_llm_segmentation_cache,
        ),
    )


def summarize_segmentation_metadata(
    *,
    metadata: dict[str, Any] | None,
    segmentation_log: list[dict[str, Any]] | None,
    use_llm_segmentation: bool,
    bypass_llm_segmentation_cache: bool = False,
) -> dict[str, Any]:
    meta = metadata if isinstance(metadata, dict) else {}
    logs = [item for item in (segmentation_log or []) if isinstance(item, dict)]
    source = str(meta.get("segmentation_source") or "unknown")
    cache_hit = any(item.get("status") == "cache_hit" or item.get("cache_hit") is True for item in logs)
    cache_bypassed = any(item.get("status") == "cache_bypassed" for item in logs) or bypass_llm_segmentation_cache
    legacy_fallback = any(
        item.get("status") == "fallback_to_legacy_chunks"
        or item.get("fallback_mode") == "legacy_json_word_chunks"
        or item.get("mode") == "legacy_json_word_chunks"
        for item in logs
    )
    heuristic = source == "heuristic" or any(item.get("source") == "heuristic" for item in logs)
    compact = any(item.get("mode") == "compact_full_words" and item.get("status") == "planned" for item in logs)

    if legacy_fallback:
        mode = "legacy_json_word_chunks"
        label = "Legacy fallback"
        fallback = True
        source = "llm"
    elif heuristic:
        mode = "heuristic"
        label = "Heuristic fallback" if use_llm_segmentation else "Heuristic"
        fallback = use_llm_segmentation
    elif compact or source == "llm":
        mode = "compact_full_words" if compact else "unknown"
        label = "Full compact" if compact else "LLM segmentation"
        fallback = False
        source = "llm"
    else:
        mode = "unknown"
        label = "Unknown"
        fallback = False

    detail_entry = next(
        (item for item in logs if item.get("mode") in {"compact_full_words", "legacy_json_word_chunks"}),
        {},
    )
    fallback_entry = next((item for item in logs if item.get("status") == "fallback_to_legacy_chunks"), {})
    prompt_version = detail_entry.get("prompt_version") or fallback_entry.get("prompt_version")
    model = detail_entry.get("model") or fallback_entry.get("model")
    reasoning_effort = detail_entry.get("reasoning_effort") or fallback_entry.get("reasoning_effort")

    result: dict[str, Any] = {
        "segmentation_source": source,
        "segmentation_mode": mode,
        "segmentation_label": label,
        "fallback": fallback,
        "cache_hit": cache_hit,
        "cache_bypassed": cache_bypassed,
    }
    if model:
        result["model"] = model
    if reasoning_effort:
        result["reasoning_effort"] = reasoning_effort
    if prompt_version:
        result["prompt_version"] = prompt_version
    return result


def _append_chalna_llm_io_logs(
    log_path: str | None,
    result_data: dict[str, Any],
    *,
    task_id: str,
    endpoint: str,
) -> None:
    if not log_path:
        return

    try:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as f:
            for key in ("segmentation_log", "refinement_log"):
                logs = result_data.get(key)
                if not isinstance(logs, list):
                    continue
                for item in logs:
                    if not isinstance(item, dict) or item.get("status") != "llm_io":
                        continue
                    entry = dict(item)
                    entry.setdefault("timestamp", timestamp)
                    entry["source"] = "chalna"
                    entry["chalna_log_key"] = key
                    entry["external_task_id"] = task_id
                    entry["endpoint"] = endpoint
                    f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        logger.exception("Failed to append Chalna LLM IO logs to %s", log_path)


def _submit_and_poll(
    *,
    endpoint: str,
    source: Path,
    data: dict[str, str],
    raw_json: Path | None = None,
    on_status: StatusCallback | None,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[dict[str, Any], str]:
    base_url = settings.chalna_url.rstrip("/")

    with httpx.Client(timeout=60.0) as client:
        with source.open("rb") as source_file:
            files: dict[str, Any] = {
                "file": (source.name, source_file, "application/octet-stream"),
            }
            if raw_json is not None:
                with raw_json.open("rb") as raw_json_file:
                    files["scribe_response"] = (
                        raw_json.name,
                        raw_json_file,
                        "application/json",
                    )
                    response = client.post(f"{base_url}{endpoint}", files=files, data=data)
            else:
                response = client.post(f"{base_url}{endpoint}", files=files, data=data)

    if response.status_code != 200:
        raise ChalnaClientError(f"Chalna submit failed: {response.text[:500]}")

    submitted = response.json()
    job_id = submitted.get("job_id") or submitted.get("task_id")
    if not job_id:
        raise ChalnaClientError(f"Chalna submit response missing job_id: {submitted}")

    if on_status:
        on_status({"job_id": job_id, "status": submitted.get("status", "queued")})

    started = time.monotonic()
    with httpx.Client(timeout=60.0) as client:
        while True:
            if time.monotonic() - started > timeout_seconds:
                raise ChalnaClientError(f"Chalna transcription timed out after {timeout_seconds:.0f}s")

            status_response = client.get(f"{base_url}/jobs/{job_id}")
            if status_response.status_code != 200:
                raise ChalnaClientError(f"Chalna status failed: {status_response.text[:500]}")

            payload = status_response.json()
            if on_status:
                on_status(payload)

            status = payload.get("status")
            if status == "completed":
                return _coerce_result(payload.get("result")), str(job_id)

            if status == "failed":
                raise ChalnaClientError(payload.get("error") or "Chalna transcription failed")

            time.sleep(poll_interval_seconds)


def _coerce_result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ChalnaClientError("Chalna completed without JSON result") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ChalnaClientError("Chalna completed without usable result")


def _segments_to_srt(segments: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(segments, 1):
        start = _seconds_to_srt_time(float(segment.get("start_time", segment.get("start", 0.0)) or 0.0))
        end = _seconds_to_srt_time(float(segment.get("end_time", segment.get("end", 0.0)) or 0.0))
        text = str(segment.get("text", "")).strip()
        speaker = segment.get("speaker_id") or segment.get("speaker")
        if speaker:
            text = f"[{speaker}] {text}"
        blocks.append(f"{index}\n{start} --> {end}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _seconds_to_srt_time(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    hours = ms // 3600000
    ms %= 3600000
    minutes = ms // 60000
    ms %= 60000
    secs = ms // 1000
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
