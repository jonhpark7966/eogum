"""Attach Eogum speaker labels to detected overlap intervals."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def enrich_overlap_speaker_mapping_files(
    *,
    overlap_path: str | Path,
    segments_path: str | Path,
) -> dict[str, Any]:
    """Enrich overlap and segment JSON files with interval-level mapped speakers."""
    overlap_file = Path(overlap_path)
    segments_file = Path(segments_path)
    overlap_payload = json.loads(overlap_file.read_text(encoding="utf-8"))
    segments_payload = json.loads(segments_file.read_text(encoding="utf-8"))

    enriched_overlap, enriched_segments, summary = enrich_overlap_speaker_mapping(
        overlap_payload,
        segments_payload,
    )

    overlap_file.write_text(
        json.dumps(enriched_overlap, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    segments_file.write_text(
        json.dumps(enriched_segments, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def enrich_overlap_speaker_mapping(
    overlap_payload: dict[str, Any],
    segments_payload: dict[str, Any] | list[Any],
) -> tuple[dict[str, Any], dict[str, Any] | list[Any], dict[str, Any]]:
    """Return copies of overlap/segments payloads with mapped speaker metadata."""
    overlap = deepcopy(overlap_payload)
    segments_container, segments = _copy_segments_payload(segments_payload)
    normalized_segments = [
        _normalize_segment(segment)
        for segment in segments
        if isinstance(segment, dict)
    ]
    normalized_segments = [segment for segment in normalized_segments if segment is not None]

    raw_intervals = overlap.get("intervals")
    intervals = raw_intervals if isinstance(raw_intervals, list) else []
    interval_mapping: dict[tuple[int, int], dict[str, Any]] = {}
    mapped_interval_count = 0

    for item in intervals:
        if not isinstance(item, dict):
            continue
        start_ms = _coerce_interval_ms(item, "start_ms", "start_time", "start")
        end_ms = _coerce_interval_ms(item, "end_ms", "end_time", "end")
        if start_ms is None or end_ms is None or end_ms <= start_ms:
            continue

        pyannote_speakers = _string_list(item.get("pyannote_speakers") or item.get("speakers"))
        mapped_speakers = _mapped_speakers_for_interval(start_ms, end_ms, normalized_segments)
        mapping_method = "segment_intersection" if mapped_speakers else "none"
        if mapped_speakers:
            mapped_interval_count += 1

        if pyannote_speakers:
            item["pyannote_speakers"] = pyannote_speakers
        item["mapped_speakers"] = mapped_speakers
        item["speaker_mapping_method"] = mapping_method
        interval_mapping[(start_ms, end_ms)] = {
            "pyannote_speakers": pyannote_speakers,
            "mapped_speakers": mapped_speakers,
            "speaker_mapping_method": mapping_method,
        }

    overlap["intervals"] = intervals

    enriched_segment_count = 0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        overlap_meta = segment.get("overlap_protection")
        if not isinstance(overlap_meta, dict):
            continue
        if _enrich_segment_overlap_metadata(overlap_meta, interval_mapping):
            enriched_segment_count += 1

    summary = {
        "schema_version": "overlap_speaker_mapping/v1",
        "method": "segment_intersection",
        "intervals": len([item for item in intervals if isinstance(item, dict)]),
        "mapped_intervals": mapped_interval_count,
        "segments": len(segments),
        "enriched_segments": enriched_segment_count,
    }
    overlap["speaker_mapping"] = summary
    return overlap, segments_container, summary


def _copy_segments_payload(payload: dict[str, Any] | list[Any]) -> tuple[dict[str, Any] | list[Any], list[Any]]:
    if isinstance(payload, dict):
        copied = deepcopy(payload)
        raw_segments = copied.get("segments")
        segments = raw_segments if isinstance(raw_segments, list) else []
        copied["segments"] = segments
        return copied, segments

    segments = deepcopy(payload) if isinstance(payload, list) else []
    return segments, segments


def _normalize_segment(segment: dict[str, Any]) -> dict[str, Any] | None:
    start_ms = _coerce_interval_ms(segment, "start_ms", "start_time", "start")
    end_ms = _coerce_interval_ms(segment, "end_ms", "end_time", "end")
    if start_ms is None or end_ms is None or end_ms <= start_ms:
        return None
    return {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "speakers": _speaker_labels_for_segment(segment),
    }


def _speaker_labels_for_segment(segment: dict[str, Any]) -> list[str]:
    overlap_meta = segment.get("overlap_protection")
    speaker = segment.get("speaker_id", segment.get("speaker"))
    if speaker == "mixed" and isinstance(overlap_meta, dict):
        speakers = _string_list(overlap_meta.get("speaker_ids"))
        if speakers:
            return speakers
    if isinstance(speaker, str) and speaker and speaker != "mixed":
        return [speaker]
    return []


def _mapped_speakers_for_interval(
    start_ms: int,
    end_ms: int,
    segments: list[dict[str, Any]],
) -> list[str]:
    speakers: set[str] = set()
    for segment in segments:
        if segment["start_ms"] < end_ms and segment["end_ms"] > start_ms:
            speakers.update(segment["speakers"])
    return sorted(speakers)


def _enrich_segment_overlap_metadata(
    overlap_meta: dict[str, Any],
    interval_mapping: dict[tuple[int, int], dict[str, Any]],
) -> bool:
    raw_intervals = overlap_meta.get("overlap_intervals_ms")
    if not isinstance(raw_intervals, list):
        return False

    mapped_speakers: set[str] = set()
    pyannote_speakers: set[str] = set()
    touched = False
    any_mapped = False

    for item in raw_intervals:
        if not isinstance(item, dict):
            continue
        start_ms = _coerce_interval_ms(item, "start_ms", "start_time", "start")
        end_ms = _coerce_interval_ms(item, "end_ms", "end_time", "end")
        mapping = interval_mapping.get((start_ms, end_ms)) if start_ms is not None and end_ms is not None else None
        if mapping is None:
            continue

        item["mapped_speakers"] = list(mapping["mapped_speakers"])
        item["speaker_mapping_method"] = mapping["speaker_mapping_method"]
        if mapping["pyannote_speakers"]:
            item["pyannote_speakers"] = list(mapping["pyannote_speakers"])
        mapped_speakers.update(mapping["mapped_speakers"])
        pyannote_speakers.update(mapping["pyannote_speakers"])
        any_mapped = any_mapped or bool(mapping["mapped_speakers"])
        touched = True

    if not touched:
        return False

    overlap_meta["mapped_speakers"] = sorted(mapped_speakers)
    if pyannote_speakers:
        overlap_meta["pyannote_speakers"] = sorted(pyannote_speakers)
    overlap_meta["speaker_mapping_method"] = "segment_intersection" if any_mapped else "none"
    return True


def _coerce_interval_ms(item: dict[str, Any], ms_key: str, seconds_key: str, fallback_key: str) -> int | None:
    value = item.get(ms_key)
    if value is not None:
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return None

    value = item.get(seconds_key, item.get(fallback_key))
    if value is None:
        return None
    try:
        return int(round(float(value) * 1000.0))
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    return sorted({str(item) for item in value if item is not None and str(item)})
