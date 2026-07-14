import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from eogum.auth import CurrentUser  # noqa: E402
from eogum.models.schemas import FinalPreviewRequest  # noqa: E402
from eogum.routes import evaluations  # noqa: E402


class _FakeQuery:
    def __init__(self, db, table_name: str):
        self.db = db
        self.table_name = table_name
        self.operation = "select"
        self.eq_filters = {}
        self.in_filters = {}
        self.insert_values = None
        self.limit_value = None
        self.order_column = None
        self.order_desc = False
        self.single_result = False
        self.maybe_single_result = False

    def select(self, _select: str):
        self.operation = "select"
        return self

    def insert(self, values: dict):
        self.operation = "insert"
        self.insert_values = values
        return self

    def eq(self, column: str, value):
        self.eq_filters[column] = value
        return self

    def in_(self, column: str, values: list):
        self.in_filters[column] = set(values)
        return self

    def order(self, column: str, desc: bool = False):
        self.order_column = column
        self.order_desc = desc
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    def single(self):
        self.single_result = True
        return self

    def maybe_single(self):
        self.maybe_single_result = True
        return self

    def execute(self):
        if self.operation == "insert":
            return self._execute_insert()
        rows = list(self._table_rows())
        rows = [row for row in rows if self._matches(row)]
        if self.order_column:
            rows.sort(key=lambda row: row.get(self.order_column) or "", reverse=self.order_desc)
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        if self.single_result or self.maybe_single_result:
            return SimpleNamespace(data=rows[0] if rows else None)
        return SimpleNamespace(data=rows)

    def _execute_insert(self):
        if self.table_name != "jobs":
            raise AssertionError(f"unexpected insert into {self.table_name}")
        job = {
            "id": f"job-{len(self.db.jobs) + 1}",
            "error_message": None,
            "created_at": f"2026-06-30T00:00:{len(self.db.jobs):02d}+00:00",
            **(self.insert_values or {}),
        }
        self.db.jobs.append(job)
        self.db.inserted_jobs.append(job)
        return SimpleNamespace(data=[job])

    def _table_rows(self):
        if self.table_name == "projects":
            return [self.db.project]
        if self.table_name == "jobs":
            return self.db.jobs
        if self.table_name == "evaluations":
            return self.db.evaluations
        return []

    def _matches(self, row: dict) -> bool:
        for column, value in self.eq_filters.items():
            if row.get(column) != value:
                return False
        for column, values in self.in_filters.items():
            if row.get(column) not in values:
                return False
        return True


class _FakeDb:
    def __init__(self, *, project: dict, jobs: list[dict] | None = None, evaluations_rows: list[dict] | None = None):
        self.project = project
        self.jobs = jobs or []
        self.evaluations = evaluations_rows or []
        self.inserted_jobs = []

    def table(self, table_name: str):
        return _FakeQuery(self, table_name)


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/projects/project-1/final-preview",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
    })


def _project(**overrides) -> dict:
    row = {
        "id": "project-1",
        "user_id": "owner-1",
        "source_duration_seconds": 10,
    }
    row.update(overrides)
    return row


def _artifact_job() -> dict:
    return {
        "id": "artifact-1",
        "project_id": "project-1",
        "user_id": "owner-1",
        "type": "podcast_cut",
        "status": "completed",
        "result_r2_keys": {"project_json": "results/project-1/source.project.json"},
        "created_at": "2026-06-30T00:00:00+00:00",
    }


def _segment(index: int, *, human: dict | None = None) -> dict:
    return {
        "index": index,
        "start_ms": index * 1000,
        "end_ms": index * 1000 + 500,
        "text": f"segment {index}",
        "ai": {
            "action": "keep",
            "reason": "content",
            "confidence": 1.0,
        },
        "human": human,
    }


def _project_json_bytes() -> bytes:
    return json.dumps({
        "source_files": [{"info": {"duration_ms": 10_000}}],
        "transcription": {"segments": [{"index": 1, "start_ms": 1000, "end_ms": 1500, "text": "segment 1"}]},
    }).encode("utf-8")


def test_public_final_preview_uses_owner_review_without_saving(monkeypatch):
    owner_human = {"action": "cut", "reason": "filler", "note": "owner"}
    db = _FakeDb(
        project=_project(),
        jobs=[_artifact_job()],
        evaluations_rows=[{
            "project_id": "project-1",
            "evaluator_id": "owner-1",
            "segments": {
                "schema_version": "saved-schema",
                "review_scope": "saved-scope",
                "join_strategy": "saved-join",
                "segments": [_segment(1, human=owner_human)],
            },
        }],
    )
    enqueued = []

    monkeypatch.setattr(evaluations, "get_db", lambda: db)
    monkeypatch.setattr(evaluations, "is_public_project_id", lambda project_id: True)
    monkeypatch.setattr(evaluations, "download_to_bytes", lambda _key: _project_json_bytes())
    monkeypatch.setattr(
        evaluations.avid,
        "review_segments",
        lambda _path: {
            "schema_version": "base-schema",
            "review_scope": "base-scope",
            "join_strategy": "base-join",
            "segments": [_segment(1), _segment(2)],
        },
    )
    monkeypatch.setattr(
        evaluations,
        "_save_evaluation_payload",
        lambda *_args, **_kwargs: pytest.fail("public readonly preview must not save evaluation"),
    )
    monkeypatch.setattr(evaluations, "enqueue_final_preview", lambda project_id, job_id: enqueued.append((project_id, job_id)))

    response = evaluations.start_final_preview(
        "project-1",
        FinalPreviewRequest(
            schema_version="client-schema",
            review_scope="client-scope",
            join_strategy="client-join",
            segments=[_segment(1, human={"action": "keep", "reason": "client", "note": ""})],
        ),
        _request(),
        current_user=None,
    )

    assert response.status == "pending"
    assert enqueued == [("project-1", "job-2")]
    inserted = db.inserted_jobs[0]
    assert inserted["result_r2_keys"]["preview_scope"] == "public_readonly"
    assert inserted["input_payload"]["schema_version"] == "saved-schema"
    assert inserted["input_payload"]["review_scope"] == "saved-scope"
    assert inserted["input_payload"]["join_strategy"] == "saved-join"
    assert inserted["input_payload"]["segments"][0]["human"] == owner_human
    assert inserted["input_payload"]["segments"][1]["human"] is None


def test_canonical_preview_overlays_only_saved_junction_repair_preference(monkeypatch):
    base_segment = _segment(1)
    base_segment["ai"] = {
        "action": "keep",
        "reason": "llm_junction_restore",
        "confidence": 0.94,
        "junction_repair": {
            "type": "llm_junction_restore",
            "original_action": "cut",
            "original_reason": "tangent",
            "repaired_to": "keep",
            "reason": "base provenance",
            "user_apply_junction_repair": True,
        },
    }
    saved_segment = _segment(1)
    saved_segment["ai"] = {
        "action": "cut",
        "reason": "tampered",
        "confidence": 0.1,
        "junction_repair": {
            "reason": "tampered provenance",
            "user_apply_junction_repair": False,
        },
    }
    db = _FakeDb(
        project=_project(),
        jobs=[_artifact_job()],
        evaluations_rows=[{
            "project_id": "project-1",
            "evaluator_id": "owner-1",
            "segments": {"segments": [saved_segment]},
        }],
    )

    monkeypatch.setattr(evaluations, "download_to_bytes", lambda _key: _project_json_bytes())
    monkeypatch.setattr(
        evaluations.avid,
        "review_segments",
        lambda _path: {
            "schema_version": "review-segments/v1",
            "review_scope": "content_segments",
            "join_strategy": "source_segment_index",
            "segments": [base_segment],
        },
    )

    payload = evaluations._canonical_final_preview_payload(db, "project-1", "owner-1")

    merged_repair = payload["segments"][0]["ai"]["junction_repair"]
    assert merged_repair["user_apply_junction_repair"] is False
    assert merged_repair["reason"] == "base provenance"
    assert payload["segments"][0]["ai"]["reason"] == "llm_junction_restore"


def test_public_final_preview_rejects_private_project(monkeypatch):
    db = _FakeDb(project=_project())
    monkeypatch.setattr(evaluations, "get_db", lambda: db)
    monkeypatch.setattr(evaluations, "is_public_project_id", lambda project_id: False)

    with pytest.raises(HTTPException) as exc:
        evaluations.start_final_preview(
            "project-1",
            FinalPreviewRequest(segments=[_segment(1)]),
            _request(),
            current_user=None,
        )

    assert exc.value.status_code == 404
    assert db.inserted_jobs == []


def test_owner_final_preview_still_saves_requested_payload(monkeypatch):
    db = _FakeDb(project=_project())
    saved_payloads = []
    enqueued = []

    monkeypatch.setattr(evaluations, "get_db", lambda: db)
    monkeypatch.setattr(
        evaluations,
        "_save_evaluation_payload",
        lambda _db, _project_id, _user_id, payload: saved_payloads.append(payload),
    )
    monkeypatch.setattr(evaluations, "enqueue_final_preview", lambda project_id, job_id: enqueued.append((project_id, job_id)))

    req = FinalPreviewRequest(
        schema_version="client-schema",
        review_scope="client-scope",
        join_strategy="client-join",
        segments=[_segment(1, human={"action": "cut", "reason": "owner", "note": ""})],
    )
    response = evaluations.start_final_preview(
        "project-1",
        req,
        _request(),
        current_user=CurrentUser(id="owner-1", email="owner@example.com", is_admin=False),
    )

    assert response.status == "pending"
    assert saved_payloads == [req.model_dump()]
    assert enqueued == [("project-1", "job-1")]
    assert db.inserted_jobs[0]["input_payload"] == req.model_dump()
    assert db.inserted_jobs[0]["result_r2_keys"]["preview_scope"] == "owner"
