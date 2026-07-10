import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from eogum.models.schemas import ProjectCreate  # noqa: E402
from eogum.services import artifacts, avid, job_runner  # noqa: E402


def _project(cut_type: str, project_id: str = "project-1") -> dict:
    return {
        "id": project_id,
        "user_id": "user-1",
        "name": cut_type,
        "cut_type": cut_type,
        "source_duration_seconds": 10,
        "source_filename": "source.mp4",
        "source_r2_key": "sources/source.mp4",
        "language": "ko",
        "settings": {
            "use_llm_segmentation": True,
            "use_llm_refinement": True,
            "edit_intensity": "heavy",
        },
    }


def _project_create_payload(cut_type: str) -> dict:
    return {
        "name": "project",
        "cut_type": cut_type,
        "source_r2_key": "sources/source.mp4",
        "source_filename": "source.mp4",
        "source_duration_seconds": 10,
        "source_size_bytes": 6,
    }


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _ProcessQuery:
    def __init__(self, db, table: str):
        self.db = db
        self.table = table
        self.op = ""

    def update(self, payload):
        self.op = "update"
        self.db.operations.append((self.table, "update", payload))
        return self

    def select(self, *args, **kwargs):
        self.op = "select"
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        if self.table == "projects" and self.op == "select":
            return _FakeResult(self.db.project)
        if self.table == "edit_reports" and self.op == "select":
            return _FakeResult([])
        return _FakeResult([{"id": self.db.job_id}])


class _ProcessDb:
    def __init__(self, project: dict, job_id: str = "job-1"):
        self.project = project
        self.job_id = job_id
        self.operations = []
        self.auth = SimpleNamespace(
            admin=SimpleNamespace(
                get_user_by_id=lambda user_id: SimpleNamespace(
                    user=SimpleNamespace(email="user@example.com")
                )
            )
        )

    def table(self, name: str):
        return _ProcessQuery(self, name)


def _write_resume_inputs(temp_root: Path, project: dict, source_bytes: bytes = b"source") -> Path:
    temp_dir = temp_root / project["id"]
    output_dir = temp_dir / "output"
    output_dir.mkdir(parents=True)
    source_path = temp_dir / "source.mp4"
    source_path.write_bytes(source_bytes)
    srt_path = temp_dir / "source.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
        encoding="utf-8",
    )
    storyline_path = output_dir / "storyline.json"
    storyline_path.write_text("{}", encoding="utf-8")

    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    project["source_sha256"] = source_sha256
    project["source_size_bytes"] = len(source_bytes)
    job_runner._write_podcast_cut_resume_state(
        temp_dir,
        project_id=project["id"],
        project=project,
        source_sha256=source_sha256,
        srt_path=str(srt_path),
        storyline_path=str(storyline_path),
    )
    return temp_dir


def _patch_process_dependencies(monkeypatch, tmp_path: Path, db: _ProcessDb) -> None:
    monkeypatch.setattr(job_runner.settings, "avid_temp_dir", tmp_path)
    monkeypatch.setattr(job_runner, "get_db", lambda: db)
    monkeypatch.setattr(job_runner.credit, "hold_credits", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner.credit, "release_hold", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner.credit, "confirm_usage", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner.email, "send_completion_email", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner.email, "send_failure_email", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner.source_cache, "upsert_source_asset", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner, "_derive_primary_source_best_effort", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        job_runner.r2,
        "download_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resume must skip downloads")),
    )
    monkeypatch.setattr(job_runner.r2, "upload_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        job_runner,
        "_download_reused_transcription_srt",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resume must skip transcription reuse")),
    )
    monkeypatch.setattr(
        job_runner,
        "_transcribe_with_scribe_v2_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resume must skip transcription")),
    )
    monkeypatch.setattr(
        job_runner.avid,
        "transcript_overview",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resume must skip storyline generation")),
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))


@pytest.mark.parametrize(
    ("cut_type", "expected_profile"),
    [("podcast_cut", "podcast"), ("ai_frontier_cut", "ai_frontier")],
)
def test_podcast_like_styles_resume_and_route_to_explicit_prompt_profile(
    monkeypatch,
    tmp_path,
    cut_type,
    expected_profile,
):
    project = _project(cut_type)
    temp_dir = _write_resume_inputs(tmp_path, project)
    db = _ProcessDb(project)
    _patch_process_dependencies(monkeypatch, tmp_path, db)
    podcast_calls = []

    def fake_podcast_cut(**kwargs):
        podcast_calls.append(kwargs)
        result_path = Path(kwargs["output_dir"]) / "result.project.avid.json"
        result_path.write_text("{}", encoding="utf-8")
        return {"project": str(result_path)}

    monkeypatch.setattr(job_runner.avid, "podcast_cut", fake_podcast_cut)
    monkeypatch.setattr(
        job_runner.avid,
        "subtitle_cut",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("podcast-like style used subtitle-cut")),
    )

    job_runner._process_project(project["id"], db.job_id)

    assert len(podcast_calls) == 1
    assert podcast_calls[0]["prompt_profile"] == expected_profile
    assert podcast_calls[0]["srt_path"] == str(temp_dir / "source.srt")
    assert podcast_calls[0]["context_path"] == str(temp_dir / "output" / "storyline.json")
    assert not temp_dir.exists()


@pytest.mark.parametrize("cut_type", ["podcast_cut", "ai_frontier_cut"])
def test_podcast_like_resume_marker_is_accepted_but_not_shared_across_styles(tmp_path, cut_type):
    project = _project(cut_type)
    temp_dir = _write_resume_inputs(tmp_path, project)

    state = job_runner._load_podcast_cut_resume_state(temp_dir, project)
    assert state is not None
    assert state["cut_type"] == cut_type
    assert job_runner._should_preserve_podcast_cut_temp(temp_dir, project, "podcast_cut")

    other_type = "podcast_cut" if cut_type == "ai_frontier_cut" else "ai_frontier_cut"
    other_project = {**project, "cut_type": other_type}
    assert job_runner._load_podcast_cut_resume_state(temp_dir, other_project) is None
    assert not job_runner._should_preserve_podcast_cut_temp(temp_dir, other_project, "podcast_cut")


def test_ai_frontier_is_an_initial_job_type():
    assert "ai_frontier_cut" in job_runner._initial_job_types


def test_avid_podcast_cut_forwards_ai_frontier_prompt_profile(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return {"artifacts": {"project": "/tmp/project.avid.json"}}

    monkeypatch.setattr(avid, "_apply_provider_args", lambda args: args)
    monkeypatch.setattr(avid, "_run_avid_json", fake_run)

    assert avid.podcast_cut("source.mp4", prompt_profile="ai_frontier") == {
        "project": "/tmp/project.avid.json"
    }
    profile_index = captured["args"].index("--prompt-profile")
    assert captured["args"][profile_index + 1] == "ai_frontier"


class _ArtifactQuery:
    def __init__(self, row: dict):
        self.row = row
        self.allowed_types = []

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, field, values):
        if field == "type":
            self.allowed_types = list(values)
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        rows = [self.row] if self.row["type"] in self.allowed_types else []
        return _FakeResult(rows)


class _ArtifactDb:
    def __init__(self, row: dict):
        self.query = _ArtifactQuery(row)

    def table(self, name: str):
        assert name == "jobs"
        return self.query


def test_ai_frontier_job_is_eligible_as_canonical_artifact_source():
    row = {
        "id": "job-1",
        "type": "ai_frontier_cut",
        "result_r2_keys": {"project": "results/project.avid.json"},
    }
    db = _ArtifactDb(row)

    assert artifacts.get_latest_artifact_job(db, "project-1") == row
    assert "ai_frontier_cut" in db.query.allowed_types


@pytest.mark.parametrize("cut_type", ["subtitle_cut", "podcast_cut", "ai_frontier_cut"])
def test_project_create_accepts_only_supported_cut_types(cut_type):
    project = ProjectCreate(**_project_create_payload(cut_type))
    assert project.cut_type == cut_type


def test_project_create_rejects_unknown_cut_type():
    with pytest.raises(ValidationError):
        ProjectCreate(**_project_create_payload("unknown_cut"))
