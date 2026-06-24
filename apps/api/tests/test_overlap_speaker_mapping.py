from eogum.services.overlap_speaker_mapping import enrich_overlap_speaker_mapping


def test_enrich_overlap_intervals_with_segment_speakers():
    overlap_payload = {
        "schema_version": "overlap_protection/v1",
        "intervals": [
            {
                "start_ms": 1000,
                "end_ms": 1500,
                "models": ["community1"],
                "speakers": ["SPEAKER_01", "SPEAKER_00"],
            },
            {
                "start_ms": 3000,
                "end_ms": 3500,
                "models": ["osd"],
            },
        ],
    }
    segments_payload = {
        "segments": [
            {"index": 1, "start_ms": 500, "end_ms": 1200, "speaker_id": "speaker_0"},
            {"index": 2, "start_ms": 1200, "end_ms": 1900, "speaker_id": "speaker_1"},
            {"index": 3, "start_ms": 3600, "end_ms": 4000, "speaker_id": "speaker_2"},
        ],
    }

    enriched_overlap, _enriched_segments, summary = enrich_overlap_speaker_mapping(
        overlap_payload,
        segments_payload,
    )

    intervals = enriched_overlap["intervals"]
    assert intervals[0]["mapped_speakers"] == ["speaker_0", "speaker_1"]
    assert intervals[0]["pyannote_speakers"] == ["SPEAKER_00", "SPEAKER_01"]
    assert intervals[0]["speaker_mapping_method"] == "segment_intersection"
    assert intervals[1]["mapped_speakers"] == []
    assert intervals[1]["speaker_mapping_method"] == "none"
    assert summary == {
        "schema_version": "overlap_speaker_mapping/v1",
        "method": "segment_intersection",
        "intervals": 2,
        "mapped_intervals": 1,
        "segments": 3,
        "enriched_segments": 0,
    }
    assert "mapped_speakers" not in overlap_payload["intervals"][0]


def test_enrich_mixed_overlap_segment_uses_source_speaker_ids():
    overlap_payload = {
        "schema_version": "overlap_protection/v1",
        "intervals": [
            {
                "start_ms": 2000,
                "end_ms": 2600,
                "models": ["community1"],
                "speakers": ["SPEAKER_02"],
            },
        ],
    }
    segments_payload = {
        "segments": [
            {
                "index": 10,
                "start_ms": 1800,
                "end_ms": 3000,
                "speaker_id": "mixed",
                "overlap_protection": {
                    "enabled": True,
                    "merged": True,
                    "speaker_ids": ["speaker_2", "speaker_0"],
                    "overlap_intervals_ms": [
                        {
                            "start_ms": 2000,
                            "end_ms": 2600,
                            "models": ["community1"],
                        },
                    ],
                },
            },
        ],
    }

    enriched_overlap, enriched_segments, summary = enrich_overlap_speaker_mapping(
        overlap_payload,
        segments_payload,
    )

    assert enriched_overlap["intervals"][0]["mapped_speakers"] == ["speaker_0", "speaker_2"]
    segment_meta = enriched_segments["segments"][0]["overlap_protection"]
    assert segment_meta["mapped_speakers"] == ["speaker_0", "speaker_2"]
    assert segment_meta["pyannote_speakers"] == ["SPEAKER_02"]
    assert segment_meta["speaker_mapping_method"] == "segment_intersection"
    assert segment_meta["overlap_intervals_ms"][0]["mapped_speakers"] == ["speaker_0", "speaker_2"]
    assert segment_meta["overlap_intervals_ms"][0]["pyannote_speakers"] == ["SPEAKER_02"]
    assert summary["mapped_intervals"] == 1
    assert summary["enriched_segments"] == 1


def test_overlap_interval_without_matching_segment_is_marked_unmapped():
    enriched_overlap, enriched_segments, summary = enrich_overlap_speaker_mapping(
        {"intervals": [{"start_ms": 100, "end_ms": 200}]},
        {"segments": [{"index": 1, "start_ms": 300, "end_ms": 400, "speaker_id": "speaker_0"}]},
    )

    assert enriched_overlap["intervals"][0]["mapped_speakers"] == []
    assert enriched_overlap["intervals"][0]["speaker_mapping_method"] == "none"
    assert enriched_segments["segments"][0]["speaker_id"] == "speaker_0"
    assert summary["mapped_intervals"] == 0
    assert summary["enriched_segments"] == 0
