import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from eogum.services import job_runner


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, db, table: str):
        self.db = db
        self.table = table
        self.op = ""
        self.payload = None

    def update(self, payload):
        self.op = "update"
        self.payload = payload
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


class _FakeDb:
    def __init__(self, project: dict, job_id: str = "job-1"):
        self.project = project
        self.job_id = job_id
        self.operations = []
        self.auth = SimpleNamespace(
            admin=SimpleNamespace(
                get_user_by_id=lambda user_id: SimpleNamespace(user=SimpleNamespace(email="user@example.com"))
            )
        )

    def table(self, name: str):
        return _FakeQuery(self, name)


def _write_resume_inputs(temp_root: Path, project: dict, source_bytes: bytes = b"source") -> Path:
    temp_dir = temp_root / project["id"]
    output_dir = temp_dir / "output"
    output_dir.mkdir(parents=True)
    source_path = temp_dir / "source.mp4"
    source_path.write_bytes(source_bytes)
    (temp_dir / "source.srt").write_text(
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
        srt_path=str(temp_dir / "source.srt"),
        storyline_path=str(storyline_path),
    )
    return temp_dir


def _project(project_id: str = "project-1") -> dict:
    return {
        "id": project_id,
        "user_id": "user-1",
        "name": "podcast",
        "cut_type": "podcast_cut",
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


def _patch_process_dependencies(monkeypatch, tmp_path: Path, project: dict, db: _FakeDb):
    monkeypatch.setattr(job_runner.settings, "avid_temp_dir", tmp_path)
    monkeypatch.setattr(job_runner, "get_db", lambda: db)
    monkeypatch.setattr(job_runner.credit, "hold_credits", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner.credit, "release_hold", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner.credit, "confirm_usage", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner.email, "send_completion_email", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner.email, "send_failure_email", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner.source_cache, "upsert_source_asset", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner.r2, "download_file", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("download should be skipped")))
    monkeypatch.setattr(job_runner.r2, "upload_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner, "_download_reused_transcription_srt", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("transcription reuse should be skipped")))
    monkeypatch.setattr(job_runner, "_transcribe_with_scribe_v2_cache", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("transcription should be skipped")))
    monkeypatch.setattr(job_runner.avid, "transcript_overview", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("storyline should be skipped")))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))


def test_podcast_cut_resume_skips_transcription_and_storyline_then_cleans_temp(monkeypatch, tmp_path):
    project = _project()
    temp_dir = _write_resume_inputs(tmp_path, project)
    db = _FakeDb(project)
    _patch_process_dependencies(monkeypatch, tmp_path, project, db)
    podcast_calls = []

    def fake_podcast_cut(**kwargs):
        podcast_calls.append(kwargs)
        result_path = Path(kwargs["output_dir"]) / "result.project.avid.json"
        result_path.write_text("{}", encoding="utf-8")
        return {"project": str(result_path)}

    monkeypatch.setattr(job_runner.avid, "podcast_cut", fake_podcast_cut)

    job_runner._process_project(project["id"], db.job_id)

    assert not temp_dir.exists()
    assert len(podcast_calls) == 1
    assert podcast_calls[0]["srt_path"] == str(tmp_path / project["id"] / "source.srt")
    assert podcast_calls[0]["context_path"] == str(tmp_path / project["id"] / "output" / "storyline.json")
    assert any(payload.get("progress") == 30 for _, _, payload in db.operations)
    assert any(payload.get("progress") == 50 for _, _, payload in db.operations)


def test_podcast_cut_failure_preserves_temp_and_resume_marker(monkeypatch, tmp_path):
    project = _project()
    temp_dir = _write_resume_inputs(tmp_path, project)
    db = _FakeDb(project)
    _patch_process_dependencies(monkeypatch, tmp_path, project, db)
    monkeypatch.setattr(
        job_runner.avid,
        "podcast_cut",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("podcast-cut failed")),
    )

    job_runner._process_project(project["id"], db.job_id)

    assert temp_dir.exists()
    assert (temp_dir / "source.srt").exists()
    assert (temp_dir / "output" / "storyline.json").exists()
    assert (temp_dir / "output" / "resume_state.json").exists()
    assert any(payload.get("status") == "failed" for _, _, payload in db.operations)


def test_podcast_cut_resume_marker_mismatch_falls_back(tmp_path):
    project = _project()
    temp_dir = _write_resume_inputs(tmp_path, project)
    changed_project = {**project, "settings": {**project["settings"], "edit_intensity": "light"}}

    assert job_runner._load_podcast_cut_resume_state(temp_dir, changed_project) is None
