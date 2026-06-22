from eogum.services.chalna import summarize_segmentation_metadata


def test_summarize_full_compact_segmentation():
    metadata = {"segmentation_source": "llm"}
    log = [{
        "status": "planned",
        "mode": "compact_full_words",
        "model": "gpt-5.5",
        "reasoning_effort": "xhigh",
        "prompt_version": "scribe_llm_segmenter_v5_punctuation_boundary",
    }]

    summary = summarize_segmentation_metadata(
        metadata=metadata,
        segmentation_log=log,
        use_llm_segmentation=True,
        bypass_llm_segmentation_cache=True,
    )

    assert summary["segmentation_label"] == "Full compact"
    assert summary["segmentation_mode"] == "compact_full_words"
    assert summary["segmentation_source"] == "llm"
    assert summary["cache_bypassed"] is True
    assert summary["fallback"] is False
    assert summary["model"] == "gpt-5.5"


def test_summarize_legacy_fallback_segmentation():
    summary = summarize_segmentation_metadata(
        metadata={"segmentation_source": "llm"},
        segmentation_log=[
            {
                "status": "fallback_to_legacy_chunks",
                "source_mode": "compact_full_words",
                "fallback_mode": "legacy_json_word_chunks",
                "prompt_version": "prompt-v1",
            },
            {"status": "planned", "mode": "legacy_json_word_chunks"},
        ],
        use_llm_segmentation=True,
    )

    assert summary["segmentation_label"] == "Legacy fallback"
    assert summary["segmentation_mode"] == "legacy_json_word_chunks"
    assert summary["segmentation_source"] == "llm"
    assert summary["fallback"] is True


def test_summarize_heuristic_fallback_segmentation():
    summary = summarize_segmentation_metadata(
        metadata={"segmentation_source": "heuristic"},
        segmentation_log=[{"status": "fallback", "source": "heuristic"}],
        use_llm_segmentation=True,
    )

    assert summary["segmentation_label"] == "Heuristic fallback"
    assert summary["segmentation_mode"] == "heuristic"
    assert summary["fallback"] is True
