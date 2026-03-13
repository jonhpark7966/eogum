from __future__ import annotations

import json
from pathlib import Path

import pytest

from eogum.routes.projects import (
    _plan_reprocess_steps,
    _project_json_has_extra_sources,
    _resolve_extra_source_offsets,
)


def test_project_json_has_extra_sources(tmp_path: Path):
    project_json = tmp_path / "project.avid.json"
    project_json.write_text(
        json.dumps({"source_files": [{"id": "main"}, {"id": "extra"}]}),
        encoding="utf-8",
    )

    assert _project_json_has_extra_sources(project_json) is True


def test_project_json_has_no_extra_sources(tmp_path: Path):
    project_json = tmp_path / "project.avid.json"
    project_json.write_text(
        json.dumps({"source_files": [{"id": "main"}]}),
        encoding="utf-8",
    )

    assert _project_json_has_extra_sources(project_json) is False


def test_resolve_extra_source_offsets_returns_none_when_missing():
    assert _resolve_extra_source_offsets([{"filename": "a.mp4"}, {"filename": "b.mp4"}]) is None


def test_resolve_extra_source_offsets_requires_all_or_none():
    with pytest.raises(ValueError, match="모든 extra source"):
        _resolve_extra_source_offsets(
            [
                {"filename": "a.mp4", "offset_ms": 100},
                {"filename": "b.mp4"},
            ]
        )


def test_resolve_extra_source_offsets_returns_ordered_list():
    assert _resolve_extra_source_offsets(
        [
            {"filename": "a.mp4", "offset_ms": 1200},
            {"filename": "b.mp4", "offset_ms": -300},
        ]
    ) == [1200, -300]


def test_plan_reprocess_steps_with_evaluation_and_multicam():
    assert _plan_reprocess_steps(
        has_evaluation=True,
        desired_extra_sources=True,
        current_project_has_extra_sources=True,
    ) == ["apply-evaluation", "rebuild-multicam", "export-project"]


def test_plan_reprocess_steps_with_clear_only():
    assert _plan_reprocess_steps(
        has_evaluation=False,
        desired_extra_sources=False,
        current_project_has_extra_sources=True,
    ) == ["clear-extra-sources", "export-project"]
