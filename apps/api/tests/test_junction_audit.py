import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from eogum.models.schemas import ProjectCreate, ProjectVariantCreate  # noqa: E402
from eogum.routes.projects import _validate_project_settings  # noqa: E402
from eogum.services import avid, job_runner  # noqa: E402
from eogum.services.review_payload import merge_saved_review_preferences  # noqa: E402


def _request(settings: dict | None = None) -> ProjectCreate:
    return ProjectCreate(
        name="junction audit",
        cut_type="podcast_cut",
        source_r2_key="sources/source.mp4",
        source_filename="source.mp4",
        source_duration_seconds=10,
        source_size_bytes=100,
        settings=settings or {},
    )


def test_project_create_defaults_junction_audit_to_true():
    request = _request()

    _validate_project_settings(request)

    assert request.settings["junction_audit_enabled"] is True


def test_project_create_preserves_false_and_rejects_non_boolean():
    request = _request({"junction_audit_enabled": False})
    _validate_project_settings(request)
    assert request.settings["junction_audit_enabled"] is False

    with pytest.raises(HTTPException):
        _validate_project_settings(_request({"junction_audit_enabled": "false"}))


def test_variant_contract_accepts_explicit_override_or_inheritance_sentinel():
    assert ProjectVariantCreate(edit_intensity="normal").junction_audit_enabled is None
    assert ProjectVariantCreate(
        edit_intensity="normal",
        junction_audit_enabled=False,
    ).junction_audit_enabled is False


def test_job_runner_treats_missing_or_invalid_setting_as_enabled():
    assert job_runner._output_junction_audit_enabled({"settings": {}}) is True
    assert job_runner._output_junction_audit_enabled({"settings": {"junction_audit_enabled": False}}) is False
    assert job_runner._output_junction_audit_enabled({"settings": {"junction_audit_enabled": "false"}}) is True


def test_canonical_review_merge_keeps_engine_ai_and_overlays_allowed_preferences():
    base = {
        "stats": {"junction_audit": {"restored_segment_count": 1}},
        "segments": [{
            "index": 511,
            "ai": {
                "action": "keep",
                "reason": "llm_junction_restore",
                "junction_repair": {
                    "type": "llm_junction_restore",
                    "original_action": "cut",
                    "repaired_to": "keep",
                    "reason": "engine provenance",
                    "user_apply_junction_repair": True,
                },
            },
            "human": None,
        }],
    }
    saved = {
        "segments": [{
            "index": 511,
            "ai": {
                "action": "cut",
                "reason": "stale",
                "junction_repair": {
                    "reason": "stale provenance",
                    "user_apply_junction_repair": False,
                },
            },
            "human": {"action": "keep", "reason": "essential", "note": "owner"},
        }],
    }

    merged = merge_saved_review_preferences(base, saved)

    assert merged["segments"][0]["ai"]["reason"] == "llm_junction_restore"
    repair = merged["segments"][0]["ai"]["junction_repair"]
    assert repair["reason"] == "engine provenance"
    assert repair["user_apply_junction_repair"] is False
    assert merged["segments"][0]["human"]["note"] == "owner"
    assert merged["stats"] == base["stats"]


@pytest.mark.parametrize(
    ("runner", "enabled", "expected_flag"),
    [
        (avid.subtitle_cut, False, "--no-junction-audit"),
        (avid.podcast_cut, True, "--junction-audit"),
    ],
)
def test_avid_cut_wrapper_forwards_explicit_junction_flag(monkeypatch, runner, enabled, expected_flag):
    captured = {}

    monkeypatch.setattr(avid, "_apply_provider_args", lambda args: args)

    def fake_run(args, **_kwargs):
        captured["args"] = args
        return {"artifacts": {}}

    monkeypatch.setattr(avid, "_run_avid_json", fake_run)

    if runner is avid.subtitle_cut:
        runner("source.mp4", "source.srt", junction_audit_enabled=enabled)
    else:
        runner("source.mp4", junction_audit_enabled=enabled)

    assert expected_flag in captured["args"]
