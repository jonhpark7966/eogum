"""Canonical merge helpers for engine review data and saved user preferences."""

from __future__ import annotations

from typing import Any


REVIEW_METADATA_KEYS = ("schema_version", "review_scope", "join_strategy")


def _segment_index(segment: Any) -> int | None:
    if not isinstance(segment, dict):
        return None
    try:
        return int(segment.get("index"))
    except (TypeError, ValueError):
        return None


def merge_saved_review_preferences(
    base_payload: dict,
    saved_payload: dict | None,
) -> dict:
    """Overlay only human decisions and junction-repair application preferences.

    All engine AI fields and repair provenance continue to come from the current
    project JSON. This prevents a stale or modified saved evaluation from
    replacing canonical AI decisions during preview or export.
    """
    saved_segments_by_index: dict[int, dict] = {}
    if isinstance(saved_payload, dict):
        saved_segments_by_index = {
            index: segment
            for segment in saved_payload.get("segments") or []
            if isinstance(segment, dict)
            and (index := _segment_index(segment)) is not None
        }

    merged_segments = []
    for base_segment in base_payload.get("segments") or []:
        if not isinstance(base_segment, dict):
            continue
        merged = dict(base_segment)
        saved_segment = saved_segments_by_index.get(_segment_index(merged))
        saved_human = (
            saved_segment.get("human") if isinstance(saved_segment, dict) else None
        )

        base_ai = merged.get("ai")
        saved_ai = saved_segment.get("ai") if isinstance(saved_segment, dict) else None
        if isinstance(base_ai, dict) and isinstance(saved_ai, dict):
            base_repair = base_ai.get("junction_repair")
            saved_repair = saved_ai.get("junction_repair")
            if isinstance(base_repair, dict) and isinstance(saved_repair, dict):
                saved_preference = saved_repair.get("user_apply_junction_repair")
                if isinstance(saved_preference, bool):
                    merged["ai"] = {
                        **base_ai,
                        "junction_repair": {
                            **base_repair,
                            "user_apply_junction_repair": saved_preference,
                        },
                    }

        merged["human"] = saved_human if isinstance(saved_human, dict) else None
        merged_segments.append(merged)

    merged_payload = dict(base_payload)
    for key in REVIEW_METADATA_KEYS:
        saved_value = saved_payload.get(key) if isinstance(saved_payload, dict) else None
        if saved_value is not None:
            merged_payload[key] = saved_value
    merged_payload["segments"] = merged_segments
    return merged_payload
