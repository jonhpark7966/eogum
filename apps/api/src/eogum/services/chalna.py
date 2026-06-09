"""Direct Chalna API client used when Eogum needs live transcription stages."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from eogum.config import settings


StatusCallback = Callable[[dict[str, Any]], None]


class ChalnaClientError(RuntimeError):
    """Raised when the Chalna API fails or returns an unusable response."""


def transcribe_to_srt(
    source_path: str,
    *,
    language: str = "ko",
    output_dir: str | None = None,
    context: str | None = None,
    diarize: bool = True,
    tag_audio_events: bool = True,
    num_speakers: int | None = None,
    use_llm_refinement: bool = True,
    on_status: StatusCallback | None = None,
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
        "use_llm_refinement": str(use_llm_refinement).lower(),
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
                output_path.write_text(_segments_to_srt(result_data.get("segments") or []), encoding="utf-8")
                return str(output_path)

            if status == "failed":
                raise ChalnaClientError(data.get("error") or "Chalna transcription failed")

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
