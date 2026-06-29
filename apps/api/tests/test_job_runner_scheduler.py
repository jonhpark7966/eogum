import os
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from eogum.services import job_runner  # noqa: E402


def _reset_scheduler() -> None:
    with job_runner._lock:
        for lane in job_runner._job_lanes:
            job_runner._queues[lane].clear()
            job_runner._running_lanes[lane] = 0


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_project_lane_respects_single_worker_limit(monkeypatch):
    _reset_scheduler()
    monkeypatch.setattr(job_runner.settings, "project_worker_count", 1)
    started: list[str] = []
    first_started = threading.Event()
    release = threading.Event()

    def fake_process(project_id: str, job_id: str | None) -> None:
        started.append(project_id)
        first_started.set()
        release.wait(2)

    monkeypatch.setattr(job_runner, "_process_project", fake_process)

    try:
        job_runner.enqueue("project-1", "job-1")
        assert first_started.wait(1)
        job_runner.enqueue("project-2", "job-2")

        assert started == ["project-1"]
        with job_runner._lock:
            assert len(job_runner._queues["project"]) == 1
            assert job_runner._running_lanes["project"] == 1
    finally:
        release.set()
        assert _wait_until(lambda: job_runner._running_lanes["project"] == 0)
        _reset_scheduler()

    assert started == ["project-1", "project-2"]


def test_project_lane_can_run_multiple_workers(monkeypatch):
    _reset_scheduler()
    monkeypatch.setattr(job_runner.settings, "project_worker_count", 2)
    started: list[str] = []
    started_lock = threading.Lock()
    two_started = threading.Event()
    release = threading.Event()

    def fake_process(project_id: str, job_id: str | None) -> None:
        with started_lock:
            started.append(project_id)
            if len(started) == 2:
                two_started.set()
        release.wait(2)

    monkeypatch.setattr(job_runner, "_process_project", fake_process)

    try:
        job_runner.enqueue("project-1", "job-1")
        job_runner.enqueue("project-2", "job-2")

        assert two_started.wait(1)
        with job_runner._lock:
            assert job_runner._running_lanes["project"] == 2
            assert len(job_runner._queues["project"]) == 0
    finally:
        release.set()
        assert _wait_until(lambda: job_runner._running_lanes["project"] == 0)
        _reset_scheduler()

    assert set(started) == {"project-1", "project-2"}
