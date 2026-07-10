import os
import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from eogum.services import database  # noqa: E402


def test_get_db_reuses_client_within_thread(monkeypatch):
    monkeypatch.setattr(database, "_thread_local", threading.local())
    created = []

    def fake_create_client():
        client = object()
        created.append(client)
        return client

    monkeypatch.setattr(database, "_create_db_client", fake_create_client)

    first = database.get_db()
    second = database.get_db()

    assert first is second
    assert created == [first]


def test_get_db_uses_distinct_clients_across_threads(monkeypatch):
    monkeypatch.setattr(database, "_thread_local", threading.local())
    created = []
    results = []
    lock = threading.Lock()

    def fake_create_client():
        client = object()
        with lock:
            created.append(client)
        return client

    def load_client():
        client = database.get_db()
        with lock:
            results.append(client)

    monkeypatch.setattr(database, "_create_db_client", fake_create_client)
    threads = [threading.Thread(target=load_client) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 2
    assert len(results) == 2
    assert results[0] is not results[1]


def test_create_db_client_disables_http2_and_sets_timeouts(monkeypatch):
    captured = {}
    fake_http_client = object()

    def fake_httpx_client(**kwargs):
        captured["httpx"] = kwargs
        return fake_http_client

    def fake_client_options(**kwargs):
        captured["options"] = kwargs
        return kwargs

    def fake_create_client(url, key, options):
        captured["create_client"] = (url, key, options)
        return "db-client"

    monkeypatch.setattr(database.httpx, "Client", fake_httpx_client)
    monkeypatch.setattr(database, "ClientOptions", fake_client_options)
    monkeypatch.setattr(database, "create_client", fake_create_client)

    result = database._create_db_client()

    assert result == "db-client"
    assert captured["httpx"]["http2"] is False
    assert captured["httpx"]["timeout"].read == 120.0
    assert captured["options"]["postgrest_client_timeout"].read == 120.0
    assert captured["options"]["storage_client_timeout"] == 120
    assert captured["options"]["httpx_client"] is fake_http_client
    assert captured["create_client"][0] == database.settings.supabase_url
    assert captured["create_client"][1] == database.settings.supabase_service_key
