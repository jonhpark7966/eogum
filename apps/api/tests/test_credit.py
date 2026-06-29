import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from eogum.services import credit  # noqa: E402


class _FakeRpc:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return SimpleNamespace(data=self.data)


class _FakeDb:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _FakeRpc(self.data)


def test_hold_credits_uses_atomic_rpc(monkeypatch):
    db = _FakeDb([{"user_id": "user-1"}])
    monkeypatch.setattr(credit, "get_db", lambda: db)

    credit.hold_credits("user-1", 10, "00000000-0000-0000-0000-000000000001")

    assert db.calls == [
        (
            "hold_credits_atomic",
            {
                "p_user_id": "user-1",
                "p_seconds": 10,
                "p_job_id": "00000000-0000-0000-0000-000000000001",
            },
        )
    ]


def test_hold_credits_raises_402_when_atomic_rpc_returns_no_row(monkeypatch):
    db = _FakeDb([])
    monkeypatch.setattr(credit, "get_db", lambda: db)
    monkeypatch.setattr(
        credit,
        "get_balance",
        lambda user_id: {
            "balance_seconds": 100,
            "held_seconds": 95,
            "available_seconds": 5,
        },
    )

    with pytest.raises(HTTPException) as exc:
        credit.hold_credits("user-1", 10, "00000000-0000-0000-0000-000000000001")

    assert exc.value.status_code == 402
    assert "사용 가능: 5초" in exc.value.detail


def test_confirm_usage_uses_atomic_rpc(monkeypatch):
    db = _FakeDb([{"user_id": "user-1"}])
    monkeypatch.setattr(credit, "get_db", lambda: db)

    credit.confirm_usage("user-1", 10, "00000000-0000-0000-0000-000000000001")

    assert db.calls[0][0] == "confirm_usage_atomic"


def test_confirm_usage_fails_when_atomic_rpc_returns_no_row(monkeypatch):
    db = _FakeDb([])
    monkeypatch.setattr(credit, "get_db", lambda: db)

    with pytest.raises(RuntimeError):
        credit.confirm_usage("user-1", 10, "00000000-0000-0000-0000-000000000001")


def test_release_hold_uses_atomic_rpc(monkeypatch):
    db = _FakeDb([{"user_id": "user-1"}])
    monkeypatch.setattr(credit, "get_db", lambda: db)

    credit.release_hold("user-1", 10, "00000000-0000-0000-0000-000000000001")

    assert db.calls[0][0] == "release_hold_atomic"


def test_zero_second_credit_operations_are_noops(monkeypatch):
    db = _FakeDb([{"user_id": "user-1"}])
    monkeypatch.setattr(credit, "get_db", lambda: db)

    credit.hold_credits("user-1", 0, None)
    credit.confirm_usage("user-1", 0, None)
    credit.release_hold("user-1", 0, None)

    assert db.calls == []
