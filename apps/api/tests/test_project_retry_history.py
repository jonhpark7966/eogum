import os
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from eogum.auth import CurrentUser  # noqa: E402
from eogum.routes import projects  # noqa: E402
from eogum.services import job_runner  # noqa: E402


class _RetryQuery:
    def __init__(self, db, table_name):
        self.db = db
        self.table_name = table_name
        self.operation = "select"
        self.payload = None

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def in_(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        self.db.operations.append((self.table_name, self.operation, self.payload))
        if self.table_name == "jobs" and self.operation == "select":
            self.db.job_select_count += 1
            if self.db.job_select_count == 1:
                return SimpleNamespace(data=self.db.active_jobs)
            return SimpleNamespace(data=[{"id": "old-job", "attempt_number": 3}])
        if self.table_name == "projects" and self.operation == "update":
            self.db.project = {**self.db.project, **(self.payload or {})}
            return SimpleNamespace(data=[self.db.project] if self.db.activation_succeeds else [])
        if self.table_name == "projects" and self.operation == "select":
            return SimpleNamespace(data=self.db.project)
        return SimpleNamespace(data=[])


class _RetryDb:
    def __init__(self, project, *, active_jobs=None, activation_succeeds=True):
        self.project = project
        self.active_jobs = active_jobs or []
        self.activation_succeeds = activation_succeeds
        self.operations = []
        self.job_select_count = 0

    def table(self, table_name):
        return _RetryQuery(self, table_name)


def test_retry_preserves_jobs_and_links_new_attempt(monkeypatch):
    project = {
        "id": "project-1",
        "user_id": "owner-1",
        "name": "AI Frontier",
        "status": "failed",
        "cut_type": "ai_frontier_cut",
        "source_duration_seconds": 60,
    }
    db = _RetryDb(project)
    created = []
    enqueued = []
    monkeypatch.setattr(projects, "get_db", lambda: db)
    monkeypatch.setattr(projects, "_get_accessible_project", lambda *args, **kwargs: project)
    monkeypatch.setattr(projects, "get_balance", lambda user_id: {"available_seconds": 120})

    def fake_create(_db, updated, **kwargs):
        created.append((updated, kwargs))
        return {"id": "retry-job"}

    def fake_retry_create(_db, **kwargs):
        updated = kwargs.pop("project")
        return fake_create(_db, updated, **kwargs), True

    monkeypatch.setattr(
        projects,
        "_create_retry_job_or_reuse_pending",
        fake_retry_create,
    )
    monkeypatch.setattr(projects, "enqueue", lambda project_id, job_id: enqueued.append((project_id, job_id)))

    result = projects.retry_project(
        "project-1",
        CurrentUser(id="owner-1", email="owner@example.com", is_admin=False),
    )

    assert result["status"] == "queued"
    assert created[0][1] == {"retry_of_job_id": "old-job", "attempt_number": 4}
    assert enqueued == [("project-1", "retry-job")]
    assert ("edit_reports", "delete", None) in db.operations
    assert not any(table == "jobs" and operation == "delete" for table, operation, _ in db.operations)


def test_retry_reuses_orphan_pending_job_after_activation_crash(monkeypatch):
    project = {
        "id": "project-1",
        "user_id": "owner-1",
        "status": "failed",
        "cut_type": "podcast_cut",
        "source_duration_seconds": 60,
    }
    orphan = {
        "id": "orphan-job",
        "type": "podcast_cut",
        "status": "pending",
        "retry_of_job_id": "old-job",
        "attempt_number": 2,
    }
    db = _RetryDb(project, active_jobs=[orphan])
    enqueued = []
    monkeypatch.setattr(projects, "get_db", lambda: db)
    monkeypatch.setattr(projects, "_get_accessible_project", lambda *args, **kwargs: project)
    monkeypatch.setattr(projects, "get_balance", lambda user_id: {"available_seconds": 120})
    monkeypatch.setattr(
        projects,
        "_create_retry_job_or_reuse_pending",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("orphan must be reused")),
    )
    monkeypatch.setattr(projects, "enqueue", lambda project_id, job_id: enqueued.append((project_id, job_id)))

    result = projects.retry_project(
        "project-1",
        CurrentUser(id="owner-1", email="owner@example.com", is_admin=False),
    )

    assert result["status"] == "queued"
    assert enqueued == [("project-1", "orphan-job")]


def test_concurrent_retry_keeps_unique_winning_pending_attempt(monkeypatch):
    project = {
        "id": "project-1",
        "user_id": "owner-1",
        "status": "failed",
        "cut_type": "podcast_cut",
        "source_duration_seconds": 60,
    }
    db = _RetryDb(project, activation_succeeds=False)
    enqueued = []
    monkeypatch.setattr(projects, "get_db", lambda: db)
    monkeypatch.setattr(projects, "_get_accessible_project", lambda *args, **kwargs: project)
    monkeypatch.setattr(projects, "get_balance", lambda user_id: {"available_seconds": 120})
    monkeypatch.setattr(
        projects,
        "_create_retry_job_or_reuse_pending",
        lambda *args, **kwargs: ({"id": "winning-job", "status": "pending"}, True),
    )
    monkeypatch.setattr(projects, "enqueue", lambda project_id, job_id: enqueued.append((project_id, job_id)))

    result = projects.retry_project(
        "project-1",
        CurrentUser(id="owner-1", email="owner@example.com", is_admin=False),
    )

    assert result["status"] == "queued"
    assert enqueued == []
    assert not any(
        table == "jobs" and operation == "update" and payload and payload.get("status") == "failed"
        for table, operation, payload in db.operations
    )


def test_retry_insert_conflict_reuses_partial_unique_index_winner(monkeypatch):
    winner = {
        "id": "winning-job",
        "type": "podcast_cut",
        "status": "pending",
        "retry_of_job_id": "old-job",
        "attempt_number": 2,
    }
    monkeypatch.setattr(
        projects,
        "create_initial_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unique violation")),
    )
    monkeypatch.setattr(projects, "_find_active_initial_job", lambda db, project: winner)

    job, created = projects._create_retry_job_or_reuse_pending(
        object(),
        project={
            "id": "project-1",
            "user_id": "owner-1",
            "cut_type": "podcast_cut",
        },
        retry_of_job_id="old-job",
        attempt_number=2,
    )

    assert job == winner
    assert created is False


def test_initial_job_insert_conflict_reuses_unique_index_winner(monkeypatch):
    winner = {
        "id": "winning-job",
        "type": "ai_frontier_cut",
        "status": "pending",
    }
    monkeypatch.setattr(
        projects,
        "create_initial_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unique violation")),
    )
    monkeypatch.setattr(projects, "_find_active_initial_job", lambda db, project: winner)

    job = projects._create_initial_job_or_fail(
        object(),
        {
            "id": "project-1",
            "user_id": "owner-1",
            "cut_type": "ai_frontier_cut",
        },
    )

    assert job == winner


def test_create_initial_job_records_attempt_lineage():
    class InsertQuery:
        def __init__(self):
            self.payload = None

        def insert(self, payload):
            self.payload = payload
            return self

        def execute(self):
            return SimpleNamespace(data=[{"id": "job-2", **self.payload}])

    class Db:
        def __init__(self):
            self.query = InsertQuery()

        def table(self, table_name):
            assert table_name == "jobs"
            return self.query

    db = Db()
    job = job_runner.create_initial_job(
        db,
        {
            "id": "project-1",
            "user_id": "owner-1",
            "cut_type": "podcast_cut",
            "settings": {},
        },
        retry_of_job_id="job-1",
        attempt_number=2,
    )

    assert job["retry_of_job_id"] == "job-1"
    assert job["attempt_number"] == 2
    assert job["type"] == "podcast_cut"


def test_recovery_migration_enforces_one_active_initial_job_per_project():
    migration = (ROOT.parent.parent / "supabase" / "migrations" / "013_scribe_transcription_recovery.sql")
    sql = migration.read_text(encoding="utf-8")

    assert "create unique index if not exists idx_jobs_one_active_initial_per_project" in sql
    assert "status in ('queued', 'pending', 'running')" in sql
    assert "type in ('subtitle_cut', 'podcast_cut', 'ai_frontier_cut')" in sql
