import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

SCRIPT_PATH = ROOT / "scripts" / "recover_scribe_transcript.py"
SPEC = importlib.util.spec_from_file_location("recover_scribe_transcript_script", SCRIPT_PATH)
assert SPEC and SPEC.loader
recovery = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recovery
SPEC.loader.exec_module(recovery)


def _project(**overrides):
    value = {
        "id": "project-1",
        "user_id": "user-1",
        "status": "failed",
        "cut_type": "ai_frontier_cut",
        "language": "ko",
        "source_duration_seconds": 10,
        "settings": {"diarize": True, "num_speakers": 2},
    }
    value.update(overrides)
    return value


def _provider_payload():
    return {
        "language_code": "kor",
        "audio_duration_secs": 10.0,
        "text": "hello world",
        "words": [
            {"type": "word", "text": "hello", "start": 0.0, "end": 5.0, "speaker_id": "A"},
            {"type": "word", "text": "world", "start": 3.0, "end": 9.5, "speaker_id": "B"},
        ],
    }


def _raw_srt():
    return (
        "1\n00:00:00,000 --> 00:00:05,000\n[A] hello\n\n"
        "2\n00:00:03,000 --> 00:00:09,500\n[B] world\n"
    )


def test_auto_detect_language_accepts_the_provider_detected_language():
    assert recovery._language_matches("auto", "kor")


def test_normalized_contract_accepts_diarized_overlap():
    stats = recovery._validate_transcript(
        _provider_payload(),
        project=_project(),
        transcription_id="transcription-1",
        tolerance=2.0,
    )
    srt_stats = recovery._validate_srt(
        _raw_srt(),
        expected_duration=stats["provider_duration_seconds"],
        tolerance=2.0,
    )

    assert stats["speaker_ids"] == ["A", "B"]
    assert srt_stats == {"cue_count": 2, "last_end_seconds": 9.5}


def test_normalized_contract_requires_speakers_when_diarized():
    payload = _provider_payload()
    for word in payload["words"]:
        word.pop("speaker_id")

    with pytest.raises(RuntimeError, match="no speaker IDs"):
        recovery._validate_transcript(
            payload,
            project=_project(settings={"diarize": True}),
            transcription_id="transcription-1",
            tolerance=2.0,
        )


def test_main_publishes_failed_cache_after_r2_round_trip(monkeypatch, capsys):
    project = _project()
    entry = {
        "status": "failed",
        "attempt_count": 0,
        "external_task_id": "chalna-1",
    }
    storage = {}
    completed_calls = []
    args = argparse.Namespace(
        project_id="project-1",
        transcription_id="transcription-1",
        external_task_id=None,
        provider_trace_id=None,
        provider_request_id=None,
        expected_cache_key=None,
        expected_json_sha256=None,
        expected_srt_sha256=None,
        duration_tolerance_seconds=2.0,
        apply=True,
        activate_project=False,
    )
    monkeypatch.setattr(recovery, "_parse_args", lambda: args)
    monkeypatch.setattr(recovery, "get_db", lambda: object())
    monkeypatch.setattr(
        recovery,
        "_load_project_and_cache",
        lambda db, project_id: (project, "cache-1", entry),
    )

    def fake_provider_recovery(transcription_id, output_dir):
        root = Path(output_dir)
        raw_json = root / "source.scribe.raw.json"
        raw_srt = root / "source.scribe.raw.srt"
        raw_json.write_text(json.dumps(_provider_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
        raw_srt.write_text(_raw_srt(), encoding="utf-8")
        return recovery.chalna.RawScribeResult(
            raw_json_path=str(raw_json),
            raw_srt_path=str(raw_srt),
            external_task_id="",
            provider_request_id="request-1",
            provider_transcription_id=transcription_id,
            provider_trace_id="trace-1",
        )

    monkeypatch.setattr(recovery.chalna, "recover_provider_transcript_to_files", fake_provider_recovery)
    monkeypatch.setattr(
        recovery.r2,
        "upload_file",
        lambda local, key, content_type: storage.__setitem__(key, Path(local).read_bytes()),
    )
    monkeypatch.setattr(recovery.r2, "download_to_bytes", lambda key: storage[key])
    monkeypatch.setattr(recovery.scribe_v2_cache, "get_cache_entry", lambda db, cache_key: entry)

    def fake_complete(db, **kwargs):
        completed_calls.append(kwargs)
        return {"status": "completed", **kwargs}

    monkeypatch.setattr(recovery.scribe_v2_cache, "recover_failed_cache_as_completed", fake_complete)

    assert recovery.main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["applied"] is True
    assert completed_calls[0]["attempt_count"] == 1
    assert completed_calls[0]["external_task_id"] == "chalna-1"
    assert completed_calls[0]["provider_trace_id"] == "trace-1"
    assert completed_calls[0]["provider_request_id"] == "request-1"
    assert set(storage) == {
        "cache/scribe-v2/cache-1/raw.json",
        "cache/scribe-v2/cache-1/raw.srt",
    }


def test_completed_cache_rerun_is_read_only(monkeypatch, tmp_path):
    raw_json_path = tmp_path / "raw.json"
    raw_srt_path = tmp_path / "raw.srt"
    raw_json_path.write_text(json.dumps(_provider_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
    raw_srt_path.write_text(_raw_srt(), encoding="utf-8")
    json_key = "cache/scribe-v2/cache-1/raw.json"
    srt_key = "cache/scribe-v2/cache-1/raw.srt"
    storage = {
        json_key: raw_json_path.read_bytes(),
        srt_key: raw_srt_path.read_bytes(),
    }
    monkeypatch.setattr(recovery.r2, "download_to_bytes", lambda key: storage[key])
    monkeypatch.setattr(
        recovery.r2,
        "upload_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("completed cache must not be overwritten")),
    )

    keys = recovery._verify_completed_cache_artifacts(
        cache_key="cache-1",
        entry={
            "status": "completed",
            "raw_json_r2_key": json_key,
            "raw_srt_r2_key": srt_key,
        },
        raw_json_path=raw_json_path,
        raw_srt_path=raw_srt_path,
    )

    assert keys[0:2] == (json_key, srt_key)


class _ActivationQuery:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.operation = "select"
        self.payload = None
        self.filters = []

    def select(self, *_args):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def in_(self, *_args):
        return self

    def neq(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def single(self):
        return self

    def execute(self):
        self.db.events.append((self.table, self.operation, self.payload))
        if self.table == "jobs" and self.operation == "select":
            if not self.db.jobs_select_count:
                self.db.jobs_select_count += 1
                return SimpleNamespace(data=self.db.active_jobs)
            self.db.jobs_select_count += 1
            if self.db.jobs_select_count == 2:
                return SimpleNamespace(data=[{"id": "old-job", "attempt_number": 1}])
            return SimpleNamespace(data=self.db.winning_jobs)
        if self.table == "projects" and self.operation == "update":
            self.db.project = {**self.db.project, **self.payload}
            return SimpleNamespace(data=[self.db.project] if self.db.activation_succeeds else [])
        if self.table == "projects" and self.operation == "select":
            return SimpleNamespace(data=self.db.project)
        return SimpleNamespace(data=[])


class _ActivationDb:
    def __init__(self, project, active_jobs=None, *, activation_succeeds=True, winning_jobs=None):
        self.project = project
        self.active_jobs = active_jobs or []
        self.activation_succeeds = activation_succeeds
        self.winning_jobs = winning_jobs or []
        self.jobs_select_count = 0
        self.events = []

    def table(self, table):
        return _ActivationQuery(self, table)


def test_activation_creates_linked_pending_before_project_is_queued(monkeypatch):
    project = _project()
    db = _ActivationDb(project)
    created = []
    monkeypatch.setattr(recovery, "get_balance", lambda user_id: {"available_seconds": 100})

    def fake_create(_db, _project, **kwargs):
        created.append((kwargs, len(db.events)))
        return {"id": "new-job", "status": "pending", **kwargs}

    monkeypatch.setattr(recovery, "create_initial_job", fake_create)

    job = recovery._activate_project(db, project=project)

    project_update_index = next(
        index
        for index, event in enumerate(db.events)
        if event[0] == "projects" and event[1] == "update"
    )
    assert created[0][0] == {"retry_of_job_id": "old-job", "attempt_number": 2}
    assert created[0][1] <= project_update_index
    assert job["already_active"] is False
    assert db.project["status"] == "queued"


def test_activation_resumes_orphan_pending_attempt_without_duplicate(monkeypatch):
    project = _project()
    active_job = {
        "id": "orphan-job",
        "type": "ai_frontier_cut",
        "status": "pending",
        "retry_of_job_id": "old-job",
        "attempt_number": 2,
    }
    db = _ActivationDb(project, active_jobs=[active_job])
    monkeypatch.setattr(recovery, "get_balance", lambda user_id: {"available_seconds": 100})
    monkeypatch.setattr(
        recovery,
        "create_initial_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must reuse orphan pending job")),
    )

    job = recovery._activate_project(db, project=project)

    assert job["id"] == "orphan-job"
    assert job["already_active"] is True
    assert db.project["status"] == "queued"


def test_concurrent_activation_reuses_partial_unique_index_winner(monkeypatch):
    project = _project()
    winning_job = {
        "id": "winning-job",
        "type": "ai_frontier_cut",
        "status": "pending",
        "retry_of_job_id": "old-job",
        "attempt_number": 2,
    }
    db = _ActivationDb(
        project,
        activation_succeeds=False,
        winning_jobs=[winning_job],
    )
    monkeypatch.setattr(recovery, "get_balance", lambda user_id: {"available_seconds": 100})
    monkeypatch.setattr(
        recovery,
        "create_initial_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unique violation")),
    )

    job = recovery._activate_project(db, project=project)

    assert job["id"] == "winning-job"
    assert job["already_active"] is True
    assert not any(
        table == "jobs"
        and operation == "update"
        and payload
        and payload.get("status") == "failed"
        for table, operation, payload in db.events
    )
