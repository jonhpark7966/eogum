import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from eogum.services import job_runner  # noqa: E402


def _ready_source_derived() -> dict:
    return {
        "status": "ready",
        "media_info_r2_key": "derived/sources/source/media_info.json",
        "audio_proxy_r2_key": "derived/sources/source/audio_proxy.flac",
        "audio_codec": "flac",
        "sample_rate": 16000,
        "channels": 1,
        "media_info_version": job_runner.source_derivatives.MEDIA_INFO_SCHEMA_VERSION,
    }


def test_ensure_chalna_audio_proxy_downloads_ready_audio_proxy(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    calls = []

    def fake_download(r2_key: str, local_path: str):
        calls.append((r2_key, local_path))
        Path(local_path).write_bytes(b"audio-proxy")
        return local_path

    monkeypatch.setattr(job_runner.r2, "download_file", fake_download)

    selected = job_runner._ensure_chalna_audio_proxy(
        project={"id": "project-1", "source_derived": _ready_source_derived()},
        source_path=source_path,
        temp_dir=tmp_path,
    )

    assert selected == tmp_path / "source.audio_proxy.flac"
    assert selected.read_bytes() == b"audio-proxy"
    assert calls == [("derived/sources/source/audio_proxy.flac", str(selected))]


def test_ensure_chalna_audio_proxy_reuses_freshly_generated_local_proxy(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    generated_proxy = tmp_path / "audio_proxy.flac"
    generated_proxy.write_bytes(b"generated-audio-proxy")
    monkeypatch.setattr(
        job_runner.r2,
        "download_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("generated proxy should be reused")),
    )

    selected = job_runner._ensure_chalna_audio_proxy(
        project={"id": "project-1", "source_derived": _ready_source_derived()},
        source_path=source_path,
        temp_dir=tmp_path,
    )

    assert selected == generated_proxy


def test_ensure_chalna_audio_proxy_never_falls_back_when_proxy_not_ready(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    monkeypatch.setattr(
        job_runner.r2,
        "download_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("proxy should not be downloaded")),
    )

    with pytest.raises(job_runner.AudioProxyPreparationError, match="not ready"):
        job_runner._ensure_chalna_audio_proxy(
            project={"id": "project-1", "source_derived": {}},
            source_path=source_path,
            temp_dir=tmp_path,
        )


def test_ensure_chalna_audio_proxy_rejects_invalid_metadata(tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    derived = {**_ready_source_derived(), "sample_rate": 48000}

    with pytest.raises(job_runner.AudioProxyPreparationError, match="metadata is invalid"):
        job_runner._ensure_chalna_audio_proxy(
            project={"id": "project-1", "source_derived": derived},
            source_path=source_path,
            temp_dir=tmp_path,
        )


def test_ensure_chalna_audio_proxy_rejects_empty_download(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")

    def fake_download(_r2_key: str, local_path: str):
        Path(local_path).write_bytes(b"")
        return local_path

    monkeypatch.setattr(job_runner.r2, "download_file", fake_download)

    with pytest.raises(job_runner.AudioProxyPreparationError, match="empty"):
        job_runner._ensure_chalna_audio_proxy(
            project={"id": "project-1", "source_derived": _ready_source_derived()},
            source_path=source_path,
            temp_dir=tmp_path,
        )


def test_ensure_chalna_audio_proxy_rejects_file_larger_than_chalna_limit(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")

    def fake_download(_r2_key: str, local_path: str):
        with Path(local_path).open("wb") as output:
            output.seek(job_runner.CHALNA_MAX_INPUT_BYTES)
            output.write(b"x")
        return local_path

    monkeypatch.setattr(job_runner.r2, "download_file", fake_download)

    with pytest.raises(job_runner.AudioProxyPreparationError, match="exceeds 2 GiB"):
        job_runner._ensure_chalna_audio_proxy(
            project={"id": "project-1", "source_derived": _ready_source_derived()},
            source_path=source_path,
            temp_dir=tmp_path,
        )


def test_chalna_multipart_content_type_is_flac_for_audio_proxy(tmp_path):
    assert job_runner.chalna._source_content_type(tmp_path / "source.audio_proxy.flac") == "audio/flac"
    assert job_runner.chalna._source_content_type(tmp_path / "source.mp4") == "application/octet-stream"


def test_validate_chalna_audio_proxy_refuses_original_source(tmp_path):
    source_path = tmp_path / "source.flac"
    source_path.write_bytes(b"audio")

    with pytest.raises(job_runner.AudioProxyPreparationError, match="original source"):
        job_runner._validate_chalna_audio_proxy_file(source_path, source_path=source_path)


def test_ensure_chalna_audio_proxy_requires_flac_r2_key(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    derived = {**_ready_source_derived(), "audio_proxy_r2_key": "derived/sources/source/audio_proxy.wav"}

    monkeypatch.setattr(
        job_runner.r2,
        "download_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("invalid key must fail before download")),
    )

    with pytest.raises(job_runner.AudioProxyPreparationError, match="R2 key must be FLAC"):
        job_runner._ensure_chalna_audio_proxy(
            project={"id": "project-1", "source_derived": derived},
            source_path=source_path,
            temp_dir=tmp_path,
        )


def test_ensure_chalna_audio_proxy_missing_download_fails(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    monkeypatch.setattr(job_runner.r2, "download_file", lambda *_args, **_kwargs: None)

    with pytest.raises(job_runner.AudioProxyPreparationError, match="missing"):
        job_runner._ensure_chalna_audio_proxy(
            project={"id": "project-1", "source_derived": _ready_source_derived()},
            source_path=source_path,
            temp_dir=tmp_path,
        )


def test_ensure_chalna_audio_proxy_rejects_non_flac_local_file(tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    wav_path = tmp_path / "proxy.wav"
    wav_path.write_bytes(b"audio")

    with pytest.raises(job_runner.AudioProxyPreparationError, match="must be FLAC"):
        job_runner._validate_chalna_audio_proxy_file(wav_path, source_path=source_path)


def test_ensure_chalna_audio_proxy_accepts_numeric_string_metadata(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    derived = {**_ready_source_derived(), "sample_rate": "16000", "channels": "1"}

    def fake_download(_r2_key: str, local_path: str):
        Path(local_path).write_bytes(b"proxy")
        return local_path

    monkeypatch.setattr(job_runner.r2, "download_file", fake_download)

    selected = job_runner._ensure_chalna_audio_proxy(
        project={"id": "project-1", "source_derived": derived},
        source_path=source_path,
        temp_dir=tmp_path,
    )

    assert selected.name == "source.audio_proxy.flac"


def test_ensure_chalna_audio_proxy_uses_expected_r2_key(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    downloaded = []

    def fake_download(r2_key: str, local_path: str):
        downloaded.append(r2_key)
        Path(local_path).write_bytes(b"proxy")
        return local_path

    monkeypatch.setattr(job_runner.r2, "download_file", fake_download)

    selected = job_runner._ensure_chalna_audio_proxy(
        project={"id": "project-1", "source_derived": _ready_source_derived()},
        source_path=source_path,
        temp_dir=tmp_path,
    )

    assert selected.read_bytes() == b"proxy"
    assert downloaded == ["derived/sources/source/audio_proxy.flac"]


def test_ensure_chalna_audio_proxy_does_not_use_source_path_for_ready_proxy(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")

    def fake_download(_r2_key: str, local_path: str):
        Path(local_path).write_bytes(b"proxy")
        return local_path

    monkeypatch.setattr(job_runner.r2, "download_file", fake_download)

    selected = job_runner._ensure_chalna_audio_proxy(
        project={"id": "project-1", "source_derived": _ready_source_derived()},
        source_path=source_path,
        temp_dir=tmp_path,
    )

    assert selected != source_path


def test_transcribe_reclaims_file_size_failed_cache_for_audio_proxy(monkeypatch, tmp_path):
    proxy_path = tmp_path / "source.audio_proxy.flac"
    proxy_path.write_bytes(b"proxy")
    raw_json_path = tmp_path / "source.scribe.raw.json"
    raw_srt_path = tmp_path / "source.scribe.raw.srt"
    claimed = []
    raw_calls = []
    refined_calls = []
    completed = []

    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "get_cache_entry",
        lambda db, cache_key: {
            "status": "failed",
            "error_message": 'Chalna submit failed: {"error_code":"E1004","error_type":"FileTooLargeError"}',
        },
    )
    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "claim_failed_entry_for_retry",
        lambda db, cache_key: claimed.append(cache_key) or {"status": "running"},
    )

    def fake_raw_transcribe(source_path, **kwargs):
        raw_calls.append(source_path)
        raw_json_path.write_text("{}", encoding="utf-8")
        raw_srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
        return job_runner.chalna.RawScribeResult(
            raw_json_path=str(raw_json_path),
            raw_srt_path=str(raw_srt_path),
            external_task_id="chalna-1",
        )

    monkeypatch.setattr(job_runner.chalna, "transcribe_raw_scribe_to_files", fake_raw_transcribe)
    monkeypatch.setattr(job_runner.r2, "upload_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "mark_cache_completed",
        lambda *args, **kwargs: completed.append(kwargs),
    )
    monkeypatch.setattr(
        job_runner.chalna,
        "transcribe_from_scribe_response_to_srt",
        lambda source_path, raw_json_path_arg, **kwargs: (
            refined_calls.append((source_path, raw_json_path_arg))
            or job_runner.chalna.TranscriptionSrtResult(
                srt_path=str(raw_srt_path),
                external_task_id="chalna-2",
                metadata={},
                segmentation_log=[],
                processing_metadata={},
            )
        ),
    )

    result = job_runner._transcribe_with_scribe_v2_cache(
        object(),
        job_id="job-1",
        project={"source_size_bytes": 123},
        source_path=str(proxy_path),
        output_dir=tmp_path,
        source_sha256="sha",
        language="en",
        transcription_context=None,
        diarize=True,
        tag_audio_events=True,
        num_speakers=None,
        use_llm_segmentation=True,
        use_llm_refinement=True,
        bypass_llm_segmentation_cache=False,
        segmentation_boundary_rule=job_runner.DEFAULT_SEGMENTATION_BOUNDARY_RULE,
        retry_failed_size_cache=True,
    )

    assert result.srt_path == str(raw_srt_path)
    assert raw_calls == [str(proxy_path)]
    assert refined_calls == [(str(proxy_path), str(raw_json_path))]
    assert len(claimed) == 1
    assert len(completed) == 1


def test_transcribe_does_not_reclaim_non_size_failed_cache(monkeypatch, tmp_path):
    proxy_path = tmp_path / "source.audio_proxy.flac"
    proxy_path.write_bytes(b"proxy")
    reclaimed = []
    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "get_cache_entry",
        lambda db, cache_key: {"status": "failed", "error_message": "Scribe authentication failed"},
    )
    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "claim_failed_entry_for_retry",
        lambda *args, **kwargs: reclaimed.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="Scribe authentication failed"):
        job_runner._transcribe_with_scribe_v2_cache(
            object(),
            job_id="job-1",
            project={"source_size_bytes": 123},
            source_path=str(proxy_path),
            output_dir=tmp_path,
            source_sha256="sha",
            language="en",
            transcription_context=None,
            diarize=True,
            tag_audio_events=True,
            num_speakers=None,
            use_llm_segmentation=True,
            use_llm_refinement=True,
            bypass_llm_segmentation_cache=False,
            segmentation_boundary_rule=job_runner.DEFAULT_SEGMENTATION_BOUNDARY_RULE,
            retry_failed_size_cache=True,
        )

    assert reclaimed == []


def test_claim_failed_cache_entry_is_conditional_and_clears_stale_outputs():
    class Result:
        data = [{"status": "running"}]

    class Query:
        def __init__(self):
            self.payload = None
            self.filters = []

        def update(self, payload):
            self.payload = payload
            return self

        def eq(self, key, value):
            self.filters.append((key, value))
            return self

        def execute(self):
            return Result()

    class Db:
        def __init__(self):
            self.query = Query()

        def table(self, name):
            assert name == "scribe_v2_cache_entries"
            return self.query

    db = Db()
    claimed = job_runner.scribe_v2_cache.claim_failed_entry_for_retry(db, cache_key="cache-1")

    assert claimed == {"status": "running"}
    assert db.query.filters == [("cache_key", "cache-1"), ("status", "failed")]
    assert db.query.payload == {
        "status": "running",
        "raw_json_r2_key": None,
        "raw_srt_r2_key": None,
        "external_task_id": None,
        "error_message": None,
        "completed_at": None,
        "last_used_at": "now()",
    }
