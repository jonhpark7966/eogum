"""Direct Chalna API client used when Eogum needs live transcription stages."""

from __future__ import annotations

import json
import logging
import time
from contextlib import ExitStack
from collections.abc import Callable
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from eogum.config import settings


logger = logging.getLogger(__name__)

StatusCallback = Callable[[dict[str, Any]], None]
DEFAULT_SEGMENTATION_BOUNDARY_RULE = "word_boundary"


class ChalnaClientError(RuntimeError):
    """Raised when Chalna fails, with machine-readable recovery details."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True)
class RawScribeResult:
    raw_json_path: str
    raw_srt_path: str
    external_task_id: str
    provider_request_id: str | None = None
    provider_transcription_id: str | None = None
    provider_trace_id: str | None = None


@dataclass(frozen=True)
class TranscriptionSrtResult:
    srt_path: str
    external_task_id: str
    metadata: dict[str, Any]
    segmentation_log: list[dict[str, Any]]
    processing_metadata: dict[str, Any]
    segments_json_path: str | None = None


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
    segmentation_boundary_rule: str = DEFAULT_SEGMENTATION_BOUNDARY_RULE,
    overlap_intervals_path: str | None = None,
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
        "segmentation_boundary_rule": segmentation_boundary_rule,
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
                files={"file": (source.name, file_obj, _source_content_type(source))},
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

    result_data, task_id, completed_status = _submit_and_poll(
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

    _write_raw_scribe_files(result_data, raw_json_path=raw_json_path, raw_srt_path=raw_srt_path)
    return RawScribeResult(
        raw_json_path=str(raw_json_path),
        raw_srt_path=str(raw_srt_path),
        external_task_id=task_id,
        provider_request_id=_nonempty_string(completed_status.get("provider_request_id")),
        provider_transcription_id=_nonempty_string(completed_status.get("provider_transcription_id")),
        provider_trace_id=_nonempty_string(completed_status.get("provider_trace_id")),
    )


def get_job_status(job_id: str) -> dict[str, Any] | None:
    """Read a previously accepted Chalna task without creating provider work."""
    base_url = settings.chalna_url.rstrip("/")
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.get(f"{base_url}/jobs/{job_id}")
    except httpx.HTTPError as exc:
        raise ChalnaClientError(
            f"Chalna status lookup failed for accepted task {job_id}: {exc}",
            details={
                "external_task_id": job_id,
                "failure_kind": "chalna_status_connection",
                "retryable": True,
                "resubmit_safe": False,
            },
        ) from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise ChalnaClientError(
            f"Chalna status lookup failed: {response.text[:500]}",
            details={
                "external_task_id": job_id,
                "failure_kind": "chalna_status_http",
                "retryable": response.status_code >= 500,
                "resubmit_safe": False,
            },
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ChalnaClientError("Chalna status response is not a JSON object")
    return payload


def resume_raw_scribe_job_to_files(
    job_id: str,
    *,
    output_dir: str,
    on_status: StatusCallback | None = None,
    timeout_seconds: float = 7200.0,
    poll_interval_seconds: float = 1.0,
) -> RawScribeResult | None:
    """Resume polling an accepted Chalna task; never submits a replacement."""
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    raw_json_path = output_root / "source.scribe.raw.json"
    raw_srt_path = output_root / "source.scribe.raw.srt"
    started = time.monotonic()

    while True:
        if time.monotonic() - started > timeout_seconds:
            raise ChalnaClientError(
                f"Chalna transcription timed out after {timeout_seconds:.0f}s",
                details={
                    "external_task_id": job_id,
                    "failure_kind": "chalna_status_timeout",
                    "retryable": True,
                    "resubmit_safe": False,
                },
            )
        payload = get_job_status(job_id)
        if payload is None:
            return None
        if on_status:
            on_status(payload)
        status = payload.get("status")
        if status == "completed":
            result_data = _coerce_result(payload.get("result"))
            _write_raw_scribe_files(
                result_data,
                raw_json_path=raw_json_path,
                raw_srt_path=raw_srt_path,
            )
            return RawScribeResult(
                raw_json_path=str(raw_json_path),
                raw_srt_path=str(raw_srt_path),
                external_task_id=job_id,
                provider_request_id=_nonempty_string(payload.get("provider_request_id")),
                provider_transcription_id=_nonempty_string(payload.get("provider_transcription_id")),
                provider_trace_id=_nonempty_string(payload.get("provider_trace_id")),
            )
        if status == "failed":
            details = _recovery_details(payload, external_task_id=job_id)
            raise ChalnaClientError(
                str(payload.get("error") or payload.get("error_message") or "Chalna transcription failed"),
                details=details,
            )
        time.sleep(poll_interval_seconds)


def recover_provider_transcript_to_files(
    transcription_id: str,
    *,
    output_dir: str,
    include_audio_events: bool = True,
) -> RawScribeResult | None:
    """Fetch an existing provider result through Chalna's local recovery API."""
    base_url = settings.chalna_url.rstrip("/")
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.get(
                f"{base_url}/provider/transcripts/{transcription_id}",
                params={"include_audio_events": str(include_audio_events).lower()},
            )
    except httpx.HTTPError as exc:
        raise ChalnaClientError(
            f"Chalna provider transcript recovery failed: {exc}",
            details={
                "provider_transcription_id": transcription_id,
                "failure_kind": "provider_recovery_connection",
                "retryable": True,
                "resubmit_safe": False,
            },
        ) from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise ChalnaClientError(
            f"Chalna provider transcript recovery failed: {response.text[:500]}",
            details={
                "provider_transcription_id": transcription_id,
                "failure_kind": "provider_recovery_http",
                "retryable": response.status_code >= 500,
                "resubmit_safe": False,
            },
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ChalnaClientError("Chalna provider recovery response is not a JSON object")
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    raw_json_path = output_root / "source.scribe.raw.json"
    raw_srt_path = output_root / "source.scribe.raw.srt"
    _write_raw_scribe_files(payload, raw_json_path=raw_json_path, raw_srt_path=raw_srt_path)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return RawScribeResult(
        raw_json_path=str(raw_json_path),
        raw_srt_path=str(raw_srt_path),
        external_task_id="",
        provider_request_id=_nonempty_string(metadata.get("provider_request_id")),
        provider_transcription_id=(
            _nonempty_string(metadata.get("provider_transcription_id")) or transcription_id
        ),
        provider_trace_id=_nonempty_string(metadata.get("provider_trace_id")),
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
    segmentation_boundary_rule: str = DEFAULT_SEGMENTATION_BOUNDARY_RULE,
    overlap_intervals_path: str | None = None,
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
    segments_json_path = output_root / "source.segments.json"
    overlap_intervals = Path(overlap_intervals_path) if overlap_intervals_path else None
    if overlap_intervals is not None and not overlap_intervals.exists():
        raise ChalnaClientError(f"Overlap intervals JSON not found: {overlap_intervals}")

    result_data, _task_id, _completed_status = _submit_and_poll(
        endpoint="/transcribe/from-scribe/async",
        source=source,
        raw_json=raw_json,
        overlap_intervals=overlap_intervals,
        data={
            "language": language,
            "use_alignment": "false",
            "use_llm_segmentation": str(use_llm_segmentation).lower(),
            "use_llm_refinement": str(use_llm_refinement).lower(),
            "bypass_llm_segmentation_cache": str(bypass_llm_segmentation_cache).lower(),
            "segmentation_boundary_rule": segmentation_boundary_rule,
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
    result_segments = result_data.get("segments") or []
    output_path.write_text(_segments_to_srt(result_segments), encoding="utf-8")
    metadata = result_data.get("metadata") if isinstance(result_data.get("metadata"), dict) else {}
    segments_json_path.write_text(
        json.dumps(
            {
                "segments": result_segments,
                "metadata": metadata,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
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
            segmentation_boundary_rule=segmentation_boundary_rule,
        ),
        segments_json_path=str(segments_json_path),
    )


def summarize_segmentation_metadata(
    *,
    metadata: dict[str, Any] | None,
    segmentation_log: list[dict[str, Any]] | None,
    use_llm_segmentation: bool,
    bypass_llm_segmentation_cache: bool = False,
    segmentation_boundary_rule: str = DEFAULT_SEGMENTATION_BOUNDARY_RULE,
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
        "segmentation_boundary_rule": meta.get(
            "segmentation_boundary_rule",
            segmentation_boundary_rule,
        ),
        "segmentation_boundary_effective_rule": meta.get(
            "segmentation_boundary_effective_rule",
            meta.get("segmentation_boundary_rule", segmentation_boundary_rule),
        ),
    }
    boundary_stats = meta.get("segmentation_boundary_stats")
    if isinstance(boundary_stats, dict):
        result["segmentation_boundary_stats"] = boundary_stats
    overlap_protection = meta.get("overlap_protection")
    if isinstance(overlap_protection, dict):
        result["overlap_protection"] = overlap_protection
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
    overlap_intervals: Path | None = None,
    on_status: StatusCallback | None,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    base_url = settings.chalna_url.rstrip("/")

    try:
        with httpx.Client(timeout=60.0) as client:
            with ExitStack() as stack:
                source_file = stack.enter_context(source.open("rb"))
                files: dict[str, Any] = {
                    "file": (source.name, source_file, _source_content_type(source)),
                }
                if raw_json is not None:
                    raw_json_file = stack.enter_context(raw_json.open("rb"))
                    files["scribe_response"] = (
                        raw_json.name,
                        raw_json_file,
                        "application/json",
                    )
                if overlap_intervals is not None:
                    overlap_file = stack.enter_context(overlap_intervals.open("rb"))
                    files["overlap_intervals"] = (
                        overlap_intervals.name,
                        overlap_file,
                        "application/json",
                    )
                response = client.post(f"{base_url}{endpoint}", files=files, data=data)
    except httpx.HTTPError as exc:
        raise ChalnaClientError(
            f"Chalna submit connection failed: {exc}",
            details={
                "failure_kind": "chalna_submit_connection",
                "retryable": True,
                # The POST may have reached Chalna. Only Chalna can later mark it safe.
                "resubmit_safe": False,
            },
        ) from exc

    if response.status_code != 200:
        response_payload = _response_json_object(response)
        raise ChalnaClientError(
            f"Chalna submit failed: {response.text[:500]}",
            details=_recovery_details(response_payload),
        )

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
                raise ChalnaClientError(
                    f"Chalna transcription timed out after {timeout_seconds:.0f}s",
                    details={
                        "external_task_id": str(job_id),
                        "failure_kind": "chalna_status_timeout",
                        "retryable": True,
                        "resubmit_safe": False,
                    },
                )

            try:
                status_response = client.get(f"{base_url}/jobs/{job_id}")
            except httpx.HTTPError as exc:
                raise ChalnaClientError(
                    f"Chalna status failed for accepted task {job_id}: {exc}",
                    details={
                        "external_task_id": str(job_id),
                        "failure_kind": "chalna_status_connection",
                        "retryable": True,
                        "resubmit_safe": False,
                    },
                ) from exc
            if status_response.status_code != 200:
                response_payload = _response_json_object(status_response)
                raise ChalnaClientError(
                    f"Chalna status failed: {status_response.text[:500]}",
                    details=_recovery_details(
                        response_payload,
                        external_task_id=str(job_id),
                    ),
                )

            payload = status_response.json()
            if on_status:
                on_status(payload)

            status = payload.get("status")
            if status == "completed":
                return _coerce_result(payload.get("result")), str(job_id), payload

            if status == "failed":
                raise ChalnaClientError(
                    str(payload.get("error") or payload.get("error_message") or "Chalna transcription failed"),
                    details=_recovery_details(payload, external_task_id=str(job_id)),
                )

            time.sleep(poll_interval_seconds)


def _source_content_type(source: Path) -> str:
    if source.suffix.lower() == ".flac":
        return "audio/flac"
    return "application/octet-stream"


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


def _write_raw_scribe_files(
    result_data: dict[str, Any],
    *,
    raw_json_path: Path,
    raw_srt_path: Path,
) -> None:
    scribe_response = result_data.get("scribe_response")
    if not isinstance(scribe_response, dict):
        raise ChalnaClientError("Chalna raw transcription completed without Scribe raw JSON")

    raw_srt = result_data.get("raw_srt")
    if not isinstance(raw_srt, str) or not raw_srt.strip():
        raw_srt = _segments_to_srt(result_data.get("segments") or [])
    if not raw_srt.strip():
        raise ChalnaClientError("Chalna raw transcription completed without raw SRT")

    raw_json_path.write_text(
        json.dumps(scribe_response, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    raw_srt_path.write_text(raw_srt, encoding="utf-8")


def _response_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _recovery_details(
    payload: dict[str, Any],
    *,
    external_task_id: str | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if external_task_id:
        details["external_task_id"] = external_task_id
    for key in (
        "provider_request_id",
        "provider_transcription_id",
        "provider_trace_id",
        "failure_kind",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value:
            details[key] = value
    for key in ("retryable", "resubmit_safe"):
        value = payload.get(key)
        if isinstance(value, bool):
            details[key] = value
    return details


def _nonempty_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


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
