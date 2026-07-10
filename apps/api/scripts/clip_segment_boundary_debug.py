#!/usr/bin/env python3
"""Download project segment boundary debug artifacts.

Run from apps/api:
  PYTHONPATH=src .venv/bin/python scripts/clip_segment_boundary_debug.py \
    --project-id <project-id> \
    --segment-id <segment-index>

Outputs:
  /tmp/eogum/segment_boundary_debug/<project-id>/segment_<segment-id>/
    metadata.json
    project.avid.json
    review_segments.json
    source.<ext>
    source.scribe.raw.json        (when a completed Scribe cache exists)
    segment_<id>.scribe_words.wav
    segment_<id>.final_review.wav
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eogum.config import settings  # noqa: E402
from eogum.services import r2, scribe_v2_cache  # noqa: E402
from eogum.services.artifacts import get_latest_artifact_job  # noqa: E402
from eogum.services.database import get_db  # noqa: E402

MAX_GAP_PADDING_MS = 500


def _bool_project_setting(settings_value: dict, key: str, *, default: bool) -> bool:
    value = settings_value.get(key)
    return value if isinstance(value, bool) else default


def _optional_int_project_setting(settings_value: dict, key: str) -> int | None:
    value = settings_value.get(key)
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _require_int(value: Any, *, field: str) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"missing or invalid {field}: {value!r}") from exc


def _load_project(db, project_id: str) -> dict:
    result = (
        db.table("projects")
        .select(
            "id, language, source_r2_key, source_filename, source_size_bytes, "
            "source_sha256, settings"
        )
        .eq("id", project_id)
        .single()
        .execute()
    )
    if not result.data:
        raise RuntimeError(f"project not found: {project_id}")
    return result.data


def _latest_project_json_key(db, project_id: str) -> tuple[str, dict]:
    job = get_latest_artifact_job(
        db,
        project_id,
        select="id, result_r2_keys, type, created_at",
    )
    if not job:
        raise RuntimeError(f"completed artifact job not found for project: {project_id}")
    result_keys = job.get("result_r2_keys") or {}
    project_json_key = result_keys.get("project_json")
    if not project_json_key:
        raise RuntimeError(f"latest artifact job has no project_json: {job.get('id')}")
    return project_json_key, job


def _download_once(r2_key: str, local_path: Path, *, force: bool, label: str) -> None:
    if local_path.exists() and local_path.stat().st_size > 0 and not force:
        print(f"reuse {label}: {local_path}")
        return
    print(f"download {label}: {r2_key} -> {local_path}")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    r2.download_file(r2_key, str(local_path))


def _segment_identity(segment: dict, position: int) -> int:
    value = segment.get("index")
    return _require_int(value, field="segment.index") if value is not None else position


def _build_local_review_segments(project_json: dict) -> dict:
    transcription = project_json.get("transcription") or {}
    source_segments = transcription.get("segments") or []
    if not source_segments:
        raise RuntimeError("project JSON has no transcription.segments")

    valid_segments: list[dict] = []
    skipped_invalid_segments: list[dict] = []
    for zero_based_position, segment in enumerate(source_segments):
        position = zero_based_position + 1
        try:
            raw_start = _require_int(segment.get("start_ms"), field="segment.start_ms")
            raw_end = _require_int(segment.get("end_ms"), field="segment.end_ms")
        except RuntimeError as exc:
            skipped_invalid_segments.append({
                "index": segment.get("index"),
                "position": position,
                "start_ms": segment.get("start_ms"),
                "end_ms": segment.get("end_ms"),
                "text": segment.get("text") or "",
                "error": str(exc),
            })
            continue

        if raw_end <= raw_start:
            skipped_invalid_segments.append({
                "index": segment.get("index"),
                "position": position,
                "start_ms": raw_start,
                "end_ms": raw_end,
                "text": segment.get("text") or "",
                "error": "end_ms must be greater than start_ms",
            })
            continue

        valid_segments.append({
            "index": _segment_identity(segment, position),
            "position": position,
            "raw_start_ms": raw_start,
            "raw_end_ms": raw_end,
            "text": segment.get("text") or "",
            "speaker": segment.get("speaker"),
        })

    if not valid_segments:
        raise RuntimeError("project JSON has no valid transcription.segments")

    review_segments: list[dict] = []
    count = len(valid_segments)
    for valid_position, segment in enumerate(valid_segments):
        raw_start = segment["raw_start_ms"]
        raw_end = segment["raw_end_ms"]
        start_ms = raw_start
        if valid_position > 0:
            previous_end = valid_segments[valid_position - 1]["raw_end_ms"]
            if previous_end < raw_start:
                start_ms = raw_start - min(MAX_GAP_PADDING_MS, (raw_start - previous_end) // 2)

        end_ms = raw_end
        if valid_position < count - 1:
            next_start = valid_segments[valid_position + 1]["raw_start_ms"]
            if raw_end < next_start:
                end_ms = raw_end + min(MAX_GAP_PADDING_MS, (next_start - raw_end) // 2)

        if end_ms <= start_ms:
            start_ms, end_ms = raw_start, raw_end

        review_segments.append({
            "index": segment["index"],
            "position": segment["position"],
            "start_ms": start_ms,
            "end_ms": end_ms,
            "raw_start_ms": raw_start,
            "raw_end_ms": raw_end,
            "text": segment["text"],
            "speaker": segment["speaker"],
        })

    payload = {
        "schema_version": "review-segments/v1-debug-local",
        "boundary_strategy": "capped_gap_padding_between_transcript_segments",
        "max_gap_padding_ms": MAX_GAP_PADDING_MS,
        "segments": review_segments,
    }
    if skipped_invalid_segments:
        payload["skipped_invalid_segments"] = skipped_invalid_segments
    return payload


def _find_review_segment(review_payload: dict, segment_id: int) -> dict:
    segments = review_payload.get("segments") or []
    for segment in segments:
        if _require_int(segment.get("index"), field="review segment.index") == segment_id:
            return segment
    for segment in segments:
        if _require_int(segment.get("position"), field="review segment.position") == segment_id:
            return segment
    for segment in review_payload.get("skipped_invalid_segments") or []:
        if segment.get("index") == segment_id or segment.get("position") == segment_id:
            raise RuntimeError(
                f"segment {segment_id} has an invalid range and was skipped: "
                f"{segment.get("start_ms")}-{segment.get("end_ms")} ({segment.get("error")})"
            )
    raise RuntimeError(f"segment not found in review payload: {segment_id}")


def _scribe_cache_params(project: dict) -> scribe_v2_cache.ScribeV2CacheParams | None:
    source_sha256 = project.get("source_sha256")
    source_size_bytes = project.get("source_size_bytes")
    if not source_sha256 or source_size_bytes in (None, ""):
        return None

    project_settings = project.get("settings") or {}
    return scribe_v2_cache.ScribeV2CacheParams(
        source_sha256=str(source_sha256),
        source_size_bytes=int(source_size_bytes),
        language=scribe_v2_cache.cache_language(project.get("language")),
        diarize=_bool_project_setting(project_settings, "diarize", default=True),
        num_speakers=_optional_int_project_setting(project_settings, "num_speakers"),
        tag_audio_events=_bool_project_setting(project_settings, "tag_audio_events", default=True),
    )


def _latest_completed_scribe_entry_for_source(db, project: dict) -> dict | None:
    source_sha256 = project.get("source_sha256")
    source_size_bytes = project.get("source_size_bytes")
    if not source_sha256 or source_size_bytes in (None, ""):
        return None
    result = (
        db.table("scribe_v2_cache_entries")
        .select("*")
        .eq("source_sha256", source_sha256)
        .eq("source_size_bytes", int(source_size_bytes))
        .eq("status", "completed")
        .order("completed_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def _find_scribe_cache_entry(db, project: dict) -> tuple[dict | None, dict | None]:
    params = _scribe_cache_params(project)
    params_payload = None
    if params is not None:
        params_payload = {
            "source_sha256": params.source_sha256,
            "source_size_bytes": params.source_size_bytes,
            "language": params.language,
            "diarize": params.diarize,
            "num_speakers": params.num_speakers,
            "tag_audio_events": params.tag_audio_events,
        }
        cache_key = scribe_v2_cache.build_scribe_v2_cache_key(params)
        entry = scribe_v2_cache.get_cache_entry(db, cache_key)
        if entry and entry.get("status") == "completed":
            return entry, params_payload

    fallback = _latest_completed_scribe_entry_for_source(db, project)
    return fallback, params_payload


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _joined_text(value: str) -> str:
    return " ".join((value or "").split())


def _time_to_ms(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 10_000:
        return int(round(number))
    return int(round(number * 1000))


def _extract_raw_words(raw_scribe: dict) -> list[dict]:
    raw_words = raw_scribe.get("words") or raw_scribe.get("segments") or []
    words: list[dict] = []
    for raw_index, item in enumerate(raw_words):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "spacing":
            continue
        text = str(item.get("text") or item.get("word") or "").strip()
        start_ms = _time_to_ms(item.get("start_ms", item.get("start")))
        end_ms = _time_to_ms(item.get("end_ms", item.get("end")))
        if not text or start_ms is None or end_ms is None or end_ms <= start_ms:
            continue
        words.append({
            "raw_index": raw_index,
            "text": text,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "speaker_id": item.get("speaker_id") or item.get("speaker"),
        })
    return words


def _span_from_words(method: str, score: float, segment_text: str, words: list[dict]) -> dict:
    return {
        "method": method,
        "score": score,
        "start_ms": words[0]["start_ms"],
        "end_ms": words[-1]["end_ms"],
        "duration_ms": words[-1]["end_ms"] - words[0]["start_ms"],
        "word_count": len(words),
        "first_word": words[0],
        "last_word": words[-1],
        "text": _joined_text(" ".join(word["text"] for word in words)) or _joined_text(segment_text),
        "words": words,
    }


def _find_scribe_words_span(raw_scribe: dict, review_segment: dict) -> dict | None:
    words = _extract_raw_words(raw_scribe)
    if not words:
        return None

    target = _joined_text(str(review_segment.get("text") or ""))
    target_compact = _compact_text(target)
    target_word_count = max(1, len(target.split()))
    max_window = max(8, target_word_count + 8)

    for start in range(len(words)):
        collected: list[dict] = []
        for end in range(start, min(len(words), start + max_window)):
            collected.append(words[end])
            joined = _joined_text(" ".join(word["text"] for word in collected))
            if joined == target:
                return _span_from_words("text_exact", 1.0, target, collected)
            compact = _compact_text(joined)
            if compact == target_compact:
                return _span_from_words("text_compact_exact", 1.0, target, collected)
            if len(compact) > len(target_compact) + 20:
                break

    raw_start = _require_int(review_segment.get("raw_start_ms"), field="raw_start_ms")
    raw_end = _require_int(review_segment.get("raw_end_ms"), field="raw_end_ms")
    overlapping = [
        word
        for word in words
        if word["end_ms"] > raw_start and word["start_ms"] < raw_end
    ]
    if overlapping:
        return _span_from_words("time_overlap", 0.0, target, overlapping)
    return None


def _render_wav_clip(source_path: Path, start_ms: int, end_ms: int, output_path: Path, *, margin_ms: int) -> None:
    if end_ms <= start_ms:
        raise RuntimeError(f"invalid clip range for {output_path.name}: {start_ms}-{end_ms}")
    start_with_margin = max(0, start_ms - margin_ms)
    end_with_margin = end_ms + margin_ms
    duration = max(0.001, (end_with_margin - start_with_margin) / 1000)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        f"{start_with_margin / 1000:.3f}",
        "-i",
        str(source_path),
        "-t",
        f"{duration:.3f}",
        "-vn",
        "-acodec",
        "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {output_path}: {result.stderr[-1000:]}")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _span_payload(start_ms: int, end_ms: int) -> dict:
    return {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "duration_ms": end_ms - start_ms,
    }


def _download_segment(args: argparse.Namespace) -> Path:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required but was not found on PATH")

    db = get_db()
    project = _load_project(db, args.project_id)
    project_json_key, artifact_job = _latest_project_json_key(db, args.project_id)

    output_root = Path(args.output_dir) if args.output_dir else settings.avid_temp_dir / "segment_boundary_debug"
    segment_dir = output_root / args.project_id / f"segment_{args.segment_id}"
    segment_dir.mkdir(parents=True, exist_ok=True)

    project_json_path = segment_dir / "project.avid.json"
    source_suffix = Path(project.get("source_filename") or "source.mp4").suffix or ".mp4"
    source_path = segment_dir / f"source{source_suffix}"
    raw_scribe_path = segment_dir / "source.scribe.raw.json"
    review_segments_path = segment_dir / "review_segments.json"
    metadata_path = segment_dir / "metadata.json"

    _download_once(project_json_key, project_json_path, force=args.force, label="project_json")
    project_json = json.loads(project_json_path.read_text(encoding="utf-8"))
    review_payload = _build_local_review_segments(project_json)
    skipped_invalid_segments = review_payload.get("skipped_invalid_segments") or []
    if skipped_invalid_segments:
        print(f"skip invalid transcript segments: {len(skipped_invalid_segments)}")
    _write_json(review_segments_path, review_payload)
    review_segment = _find_review_segment(review_payload, int(args.segment_id))

    source_key = project.get("source_r2_key")
    if not source_key:
        raise RuntimeError(f"project has no source_r2_key: {args.project_id}")
    if not args.skip_source:
        _download_once(source_key, source_path, force=args.force, label="source")
    elif not source_path.exists():
        raise RuntimeError(f"--skip-source was passed but source file is missing: {source_path}")

    scribe_entry = None
    scribe_params = None
    raw_scribe = None
    if not args.skip_raw_scribe:
        scribe_entry, scribe_params = _find_scribe_cache_entry(db, project)
        raw_json_key = scribe_entry.get("raw_json_r2_key") if scribe_entry else None
        if raw_json_key:
            _download_once(raw_json_key, raw_scribe_path, force=args.force, label="raw_scribe_json")
            raw_scribe = json.loads(raw_scribe_path.read_text(encoding="utf-8"))
        else:
            print("raw Scribe cache not found; using project raw segment span only")

    scribe_words_span = _find_scribe_words_span(raw_scribe, review_segment) if raw_scribe else None

    raw_start_ms = _require_int(review_segment.get("raw_start_ms"), field="raw_start_ms")
    raw_end_ms = _require_int(review_segment.get("raw_end_ms"), field="raw_end_ms")
    final_start_ms = _require_int(review_segment.get("start_ms"), field="start_ms")
    final_end_ms = _require_int(review_segment.get("end_ms"), field="end_ms")

    scribe_clip_start = scribe_words_span["start_ms"] if scribe_words_span else raw_start_ms
    scribe_clip_end = scribe_words_span["end_ms"] if scribe_words_span else raw_end_ms
    scribe_clip_path = segment_dir / f"segment_{args.segment_id}.scribe_words.wav"
    final_clip_path = segment_dir / f"segment_{args.segment_id}.final_review.wav"
    _render_wav_clip(source_path, scribe_clip_start, scribe_clip_end, scribe_clip_path, margin_ms=args.margin_ms)
    _render_wav_clip(source_path, final_start_ms, final_end_ms, final_clip_path, margin_ms=args.margin_ms)

    scribe_cache_metadata = None
    if scribe_entry:
        scribe_cache_metadata = {
            "cache_key": scribe_entry.get("cache_key"),
            "raw_json_r2_key": scribe_entry.get("raw_json_r2_key"),
            "entry_status": scribe_entry.get("status"),
            "params": scribe_params
            or {
                "source_sha256": scribe_entry.get("source_sha256"),
                "source_size_bytes": scribe_entry.get("source_size_bytes"),
                "language": scribe_entry.get("language"),
                "diarize": scribe_entry.get("diarize"),
                "num_speakers": scribe_entry.get("num_speakers"),
                "tag_audio_events": scribe_entry.get("tag_audio_events"),
            },
        }

    metadata = {
        "project_id": args.project_id,
        "segment_index": int(args.segment_id),
        "input": {
            "project_id": args.project_id,
            "project_json_path": str(project_json_path),
            "project_json_r2_key": project_json_key,
            "artifact_job": {
                "id": artifact_job.get("id"),
                "type": artifact_job.get("type"),
                "created_at": artifact_job.get("created_at"),
            },
            "source_path": str(source_path),
            "source_r2_key": source_key,
            "raw_scribe_json_path": str(raw_scribe_path) if raw_scribe_path.exists() else None,
            "scribe_cache": scribe_cache_metadata,
        },
        "review_segment": review_segment,
        "scribe_words_span": scribe_words_span,
        "project_raw_segment_span": _span_payload(raw_start_ms, raw_end_ms),
        "final_segment_span": _span_payload(final_start_ms, final_end_ms),
        "deltas_ms": {
            "final_start_minus_scribe_word_start": (
                final_start_ms - scribe_words_span["start_ms"] if scribe_words_span else None
            ),
            "final_end_minus_scribe_word_end": (
                final_end_ms - scribe_words_span["end_ms"] if scribe_words_span else None
            ),
            "project_raw_start_minus_scribe_word_start": (
                raw_start_ms - scribe_words_span["start_ms"] if scribe_words_span else None
            ),
            "project_raw_end_minus_scribe_word_end": (
                raw_end_ms - scribe_words_span["end_ms"] if scribe_words_span else None
            ),
        },
        "clips": {
            "scribe_words": str(scribe_clip_path),
            "final_review": str(final_clip_path),
            "margin_ms": args.margin_ms,
        },
        "debug_files": {
            "metadata": str(metadata_path),
            "review_segments": str(review_segments_path),
        },
    }
    _write_json(metadata_path, metadata)

    print("")
    print(f"wrote segment debug artifacts: {segment_dir}")
    print(f"segment text: {review_segment.get('text')}")
    print(f"raw span: {raw_start_ms}-{raw_end_ms} ms")
    print(f"final span: {final_start_ms}-{final_end_ms} ms")
    print(f"metadata: {metadata_path}")
    return segment_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Download and clip one project review segment for boundary debugging.")
    parser.add_argument("--project-id", required=True, help="Project UUID")
    parser.add_argument("--segment-id", required=True, type=int, help="Review/transcript segment index")
    parser.add_argument(
        "--output-dir",
        help="Root output directory (default: settings.avid_temp_dir/segment_boundary_debug)",
    )
    parser.add_argument("--margin-ms", type=int, default=0, help="Extra audio margin around rendered clips")
    parser.add_argument("--force", action="store_true", help="Redownload inputs even when local files exist")
    parser.add_argument("--skip-source", action="store_true", help="Reuse an existing local source file")
    parser.add_argument("--skip-raw-scribe", action="store_true", help="Do not download raw Scribe cache JSON")
    args = parser.parse_args()

    if args.margin_ms < 0:
        parser.error("--margin-ms must be >= 0")

    _download_segment(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
