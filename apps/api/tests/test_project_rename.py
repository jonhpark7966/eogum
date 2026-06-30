import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from eogum.auth import CurrentUser  # noqa: E402
from eogum.main import app  # noqa: E402
from eogum.models.schemas import ProjectUpdateRequest  # noqa: E402
from eogum.routes import projects  # noqa: E402


class _FakeProjectQuery:
    def __init__(self, db, table_name: str):
        self.db = db
        self.table_name = table_name
        self.operation = "select"
        self.update_values = None
        self.filters = {}

    def select(self, _select: str):
        return self

    def update(self, values: dict):
        self.operation = "update"
        self.update_values = values
        return self

    def eq(self, column: str, value):
        self.filters[column] = value
        return self

    def single(self):
        return self

    def execute(self):
        if self.table_name != "projects":
            return SimpleNamespace(data=[])

        if self.filters.get("id") != self.db.project.get("id"):
            return SimpleNamespace(data=None if self.operation == "select" else [])

        if self.operation == "update":
            self.db.project = {
                **self.db.project,
                **(self.update_values or {}),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.db.update_values = self.update_values
            return SimpleNamespace(data=[self.db.project])

        return SimpleNamespace(data=self.db.project)


class _FakeDb:
    def __init__(self, project: dict):
        self.project = project
        self.update_values = None

    def table(self, table_name: str):
        return _FakeProjectQuery(self, table_name)


def _project_row(**overrides) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": "project-1",
        "user_id": "owner-1",
        "name": "Original",
        "status": "completed",
        "cut_type": "podcast_cut",
        "language": "ko",
        "source_filename": "source.mp4",
        "source_duration_seconds": 10,
        "source_sha256": None,
        "source_derived": {},
        "extra_sources": [],
        "multicam_state": {},
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def test_update_project_renames_owned_project(monkeypatch):
    db = _FakeDb(_project_row())
    monkeypatch.setattr(projects, "get_db", lambda: db)

    result = projects.update_project(
        "project-1",
        ProjectUpdateRequest(name="  Renamed Project  "),
        CurrentUser(id="owner-1", email="owner@example.com", is_admin=False),
    )

    assert result["name"] == "Renamed Project"
    assert result["viewer_can_edit"] is True
    assert db.update_values == {"name": "Renamed Project"}


def test_update_project_allows_admin(monkeypatch):
    db = _FakeDb(_project_row())
    monkeypatch.setattr(projects, "get_db", lambda: db)

    result = projects.update_project(
        "project-1",
        ProjectUpdateRequest(name="Admin Rename"),
        CurrentUser(id="admin-1", email="admin@example.com", is_admin=True),
    )

    assert result["name"] == "Admin Rename"
    assert result["viewer_can_edit"] is True


def test_update_project_hides_project_from_non_owner(monkeypatch):
    db = _FakeDb(_project_row())
    monkeypatch.setattr(projects, "get_db", lambda: db)

    with pytest.raises(HTTPException) as exc:
        projects.update_project(
            "project-1",
            ProjectUpdateRequest(name="Nope"),
            CurrentUser(id="other-1", email="other@example.com", is_admin=False),
        )

    assert exc.value.status_code == 404
    assert db.update_values is None


def test_update_project_rejects_blank_name(monkeypatch):
    db = _FakeDb(_project_row())
    monkeypatch.setattr(projects, "get_db", lambda: db)

    with pytest.raises(HTTPException) as exc:
        projects.update_project(
            "project-1",
            ProjectUpdateRequest(name="   "),
            CurrentUser(id="owner-1", email="owner@example.com", is_admin=False),
        )

    assert exc.value.status_code == 400
    assert db.update_values is None


def test_update_project_rejects_too_long_name(monkeypatch):
    db = _FakeDb(_project_row())
    monkeypatch.setattr(projects, "get_db", lambda: db)

    with pytest.raises(HTTPException) as exc:
        projects.update_project(
            "project-1",
            ProjectUpdateRequest(name="x" * 121),
            CurrentUser(id="owner-1", email="owner@example.com", is_admin=False),
        )

    assert exc.value.status_code == 400
    assert db.update_values is None


def test_update_project_requires_authentication():
    client = TestClient(app)

    response = client.patch("/api/v1/projects/project-1", json={"name": "New Name"})

    assert response.status_code == 401
