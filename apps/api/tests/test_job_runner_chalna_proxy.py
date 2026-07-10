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


def test_chalna_provider_recovery_contract_writes_raw_files(monkeypatch, tmp_path):
    class Response:
        status_code = 200
        text = "ok"

        @staticmethod
        def json():
            return {
                "scribe_response": {"language_code": "kor", "words": [{"text": "hello"}]},
                "raw_srt": "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
                    "metadata": {
                        "provider_request_id": "request-1",
                        "provider_transcription_id": "transcription-1",
                    "provider_trace_id": "trace-1",
                },
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def get(url, **kwargs):
            assert url.endswith("/provider/transcripts/transcription-1")
            assert kwargs["params"] == {"include_audio_events": "true"}
            return Response()

    monkeypatch.setattr(job_runner.chalna.httpx, "Client", Client)

    result = job_runner.chalna.recover_provider_transcript_to_files(
        "transcription-1",
        output_dir=str(tmp_path),
    )

    assert result is not None
    assert result.provider_request_id == "request-1"
    assert result.provider_transcription_id == "transcription-1"
    assert result.provider_trace_id == "trace-1"
    assert Path(result.raw_json_path).read_text(encoding="utf-8").startswith("{")
    assert "hello" in Path(result.raw_srt_path).read_text(encoding="utf-8")


def test_provider_404_falls_back_to_saved_chalna_task_without_audio_events(monkeypatch, tmp_path):
    raw_json_path = tmp_path / "source.scribe.raw.json"
    raw_srt_path = tmp_path / "source.scribe.raw.srt"
    raw_json_path.write_text("{}", encoding="utf-8")
    raw_srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    provider_calls = []
    resumed = []

    def fake_provider(transcription_id, output_dir, include_audio_events):
        provider_calls.append((transcription_id, include_audio_events))
        return None

    def fake_resume(job_id, **kwargs):
        resumed.append(job_id)
        return job_runner.chalna.RawScribeResult(
            raw_json_path=str(raw_json_path),
            raw_srt_path=str(raw_srt_path),
            external_task_id=job_id,
            provider_transcription_id="transcription-1",
        )

    monkeypatch.setattr(job_runner.chalna, "recover_provider_transcript_to_files", fake_provider)
    monkeypatch.setattr(job_runner.chalna, "resume_raw_scribe_job_to_files", fake_resume)

    result = job_runner._recover_existing_raw_scribe_result(
        object(),
        cache_key="cache-1",
        entry={
            "status": "failed",
            "owner_token": "owner-1",
            "provider_transcription_id": "transcription-1",
            "external_task_id": "chalna-1",
            "tag_audio_events": False,
        },
        job_id="job-1",
        output_dir=tmp_path,
        use_llm_segmentation=False,
        use_llm_refinement=False,
        owner_token="owner-1",
        expected_status="failed",
    )

    assert result is not None
    assert provider_calls == [("transcription-1", False)]
    assert resumed == ["chalna-1"]


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
        lambda db, cache_key, **kwargs: claimed.append(cache_key)
        or {"status": "running", "owner_token": kwargs["owner_token"], "attempt_count": 1},
    )
    monkeypatch.setattr(job_runner.scribe_v2_cache, "new_owner_token", lambda: "owner-new")

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
    monkeypatch.setattr(job_runner, "_upload_and_verify_scribe_cache_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "mark_cache_completed",
        lambda *args, **kwargs: completed.append(kwargs) or True,
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


def test_transcribe_does_not_resubmit_accepted_incomplete_read(monkeypatch, tmp_path):
    proxy_path = tmp_path / "source.audio_proxy.flac"
    proxy_path.write_bytes(b"proxy")
    reclaimed = []
    submitted = []
    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "get_cache_entry",
        lambda db, cache_key: {
            "status": "failed",
            "error_message": "incomplete chunked read",
            "provider_request_id": "request-1",
            "failure_kind": "incomplete_read",
            "retryable": True,
            "resubmit_safe": False,
        },
    )
    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "claim_failed_entry_for_retry",
        lambda *args, **kwargs: reclaimed.append((args, kwargs)),
    )
    monkeypatch.setattr(
        job_runner.chalna,
        "transcribe_raw_scribe_to_files",
        lambda *args, **kwargs: submitted.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="incomplete chunked read"):
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
    assert submitted == []


def test_transcribe_recovers_provider_transcript_before_resubmitting(monkeypatch, tmp_path):
    proxy_path = tmp_path / "source.audio_proxy.flac"
    proxy_path.write_bytes(b"proxy")
    raw_json_path = tmp_path / "source.scribe.raw.json"
    raw_srt_path = tmp_path / "source.scribe.raw.srt"
    raw_json_path.write_text('{"language_code":"kor"}', encoding="utf-8")
    raw_srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    completed = []
    uploads = []
    submitted = []
    entry = {
        "status": "failed",
        "error_message": "incomplete chunked read",
        "external_task_id": "chalna-old",
        "provider_transcription_id": "transcription-1",
        "owner_token": "owner-old",
        "retryable": True,
        "resubmit_safe": False,
    }
    monkeypatch.setattr(job_runner.scribe_v2_cache, "get_cache_entry", lambda db, cache_key: entry)
    monkeypatch.setattr(
        job_runner.chalna,
        "recover_provider_transcript_to_files",
        lambda transcription_id, output_dir, **kwargs: job_runner.chalna.RawScribeResult(
            raw_json_path=str(raw_json_path),
            raw_srt_path=str(raw_srt_path),
            external_task_id="",
            provider_transcription_id=transcription_id,
            provider_trace_id="trace-1",
        ),
    )
    monkeypatch.setattr(
        job_runner.chalna,
        "transcribe_raw_scribe_to_files",
        lambda *args, **kwargs: submitted.append((args, kwargs)),
    )
    monkeypatch.setattr(
        job_runner,
        "_upload_and_verify_scribe_cache_artifact",
        lambda *args, **kwargs: uploads.append((args, kwargs)),
    )
    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "recover_failed_cache_as_completed",
        lambda *args, **kwargs: completed.append(kwargs) or {"status": "completed"},
    )
    monkeypatch.setattr(
        job_runner.chalna,
        "transcribe_from_scribe_response_to_srt",
        lambda source_path, raw_json_path_arg, **kwargs: job_runner.chalna.TranscriptionSrtResult(
            srt_path=str(raw_srt_path),
            external_task_id="chalna-refine",
            metadata={},
            segmentation_log=[],
            processing_metadata={},
        ),
    )

    result = job_runner._transcribe_with_scribe_v2_cache(
        object(),
        job_id="job-1",
        project={"source_size_bytes": 123},
        source_path=str(proxy_path),
        output_dir=tmp_path,
        source_sha256="sha",
        language="ko",
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
    assert submitted == []
    assert len(uploads) == 2
    assert completed[0]["external_task_id"] == "chalna-old"
    assert completed[0]["provider_transcription_id"] == "transcription-1"
    assert completed[0]["provider_trace_id"] == "trace-1"


@pytest.mark.parametrize("refreshed_status", ["running", "completed"])
def test_failed_cache_reread_follows_newer_authoritative_state(
    monkeypatch,
    tmp_path,
    refreshed_status,
):
    proxy_path = tmp_path / "source.audio_proxy.flac"
    proxy_path.write_bytes(b"proxy")
    failed_entry = {
        "status": "failed",
        "owner_token": "owner-old",
        "attempt_count": 1,
        "error_message": "old failure",
        "retryable": False,
        "resubmit_safe": False,
    }
    completed_entry = {
        "cache_key": "cache-1",
        "status": "completed",
        "owner_token": "owner-new",
        "raw_json_r2_key": "attempts/new/raw.json",
        "raw_srt_r2_key": "attempts/new/raw.srt",
        "hit_count": 0,
    }
    refreshed_entry = {**completed_entry, "status": refreshed_status}
    entries = iter([failed_entry, refreshed_entry])
    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "get_cache_entry",
        lambda db, cache_key: next(entries),
    )
    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "wait_for_running_entry",
        lambda db, cache_key: completed_entry,
    )

    def fake_download(key, local):
        if key.endswith("raw.json"):
            Path(local).write_text("{}", encoding="utf-8")
        else:
            Path(local).write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
                encoding="utf-8",
            )

    monkeypatch.setattr(job_runner.r2, "download_file", fake_download)
    monkeypatch.setattr(job_runner.scribe_v2_cache, "record_cache_hit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "claim_failed_entry_for_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("newer state must be followed")),
    )
    monkeypatch.setattr(
        job_runner.chalna,
        "transcribe_raw_scribe_to_files",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not resubmit")),
    )
    monkeypatch.setattr(
        job_runner.chalna,
        "transcribe_from_scribe_response_to_srt",
        lambda source_path, raw_json_path_arg, **kwargs: job_runner.chalna.TranscriptionSrtResult(
            srt_path=str(tmp_path / "source.scribe.raw.srt"),
            external_task_id="refine-1",
            metadata={},
            segmentation_log=[],
            processing_metadata={},
        ),
    )

    result = job_runner._transcribe_with_scribe_v2_cache(
        object(),
        job_id="job-1",
        project={"source_size_bytes": 123},
        source_path=str(proxy_path),
        output_dir=tmp_path,
        source_sha256="sha",
        language="ko",
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

    assert result.external_task_id == "refine-1"


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
    claimed = job_runner.scribe_v2_cache.claim_failed_entry_for_retry(
        db,
        cache_key="cache-1",
        owner_token="owner-new",
        expected_attempt_count=2,
    )

    assert claimed == {"status": "running"}
    assert db.query.filters == [
        ("cache_key", "cache-1"),
        ("status", "failed"),
        ("attempt_count", 2),
        ("retryable", True),
        ("resubmit_safe", True),
    ]
    assert db.query.payload == {
        "status": "running",
        "owner_token": "owner-new",
        "raw_json_r2_key": None,
        "raw_srt_r2_key": None,
        "external_task_id": None,
        "provider_request_id": None,
        "provider_transcription_id": None,
        "provider_trace_id": None,
        "error_message": None,
        "failure_kind": None,
        "retryable": False,
        "resubmit_safe": False,
        "completed_at": None,
        "attempt_count": 3,
        "last_attempt_at": "now()",
        "last_used_at": "now()",
    }


def test_record_provider_status_persists_recovery_contract():
    class Result:
        data = [{}]

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
    job_runner.scribe_v2_cache.record_provider_status(
        db,
        cache_key="cache-1",
        owner_token="owner-1",
        payload={
            "job_id": "chalna-1",
            "provider_request_id": "request-1",
            "provider_transcription_id": "transcription-1",
            "provider_trace_id": "trace-1",
            "failure_kind": "incomplete_read",
            "retryable": True,
            "resubmit_safe": False,
        },
    )

    assert db.query.filters == [
        ("cache_key", "cache-1"),
        ("status", "running"),
        ("owner_token", "owner-1"),
    ]
    assert db.query.payload == {
        "external_task_id": "chalna-1",
        "provider_request_id": "request-1",
        "provider_transcription_id": "transcription-1",
        "provider_trace_id": "trace-1",
        "failure_kind": "incomplete_read",
        "retryable": True,
        "resubmit_safe": False,
        "last_used_at": "now()",
    }


def test_recover_failed_cache_as_completed_is_conditional():
    class Result:
        data = [{"cache_key": "cache-1", "status": "completed"}]

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

        def is_(self, key, value):
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
    recovered = job_runner.scribe_v2_cache.recover_failed_cache_as_completed(
        db,
        cache_key="cache-1",
        raw_json_key="cache/raw.json",
        raw_srt_key="cache/raw.srt",
        external_task_id="chalna-1",
        provider_transcription_id="transcription-1",
        provider_trace_id="trace-1",
        attempt_count=1,
        expected_owner_token=None,
        expected_attempt_count=0,
    )

    assert recovered == {"cache_key": "cache-1", "status": "completed"}
    assert db.query.filters == [
        ("cache_key", "cache-1"),
        ("status", "failed"),
        ("owner_token", "null"),
        ("attempt_count", 0),
    ]
    assert db.query.payload["status"] == "completed"
    assert db.query.payload["provider_transcription_id"] == "transcription-1"
    assert db.query.payload["attempt_count"] == 1
    assert db.query.payload["retryable"] is False
    assert db.query.payload["resubmit_safe"] is False


class _CasResult:
    def __init__(self, data):
        self.data = data


class _CasQuery:
    def __init__(self, db):
        self.db = db
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

    def insert(self, payload):
        self.operation = "insert"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters.append(("eq", key, value))
        return self

    def is_(self, key, value):
        self.filters.append(("is", key, value))
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        if self.operation == "insert":
            if self.db.row is not None:
                raise RuntimeError("duplicate")
            self.db.row = dict(self.payload)
            return _CasResult([dict(self.db.row)])
        matches = self.db.row is not None
        for operation, key, value in self.filters:
            if operation == "eq":
                matches = matches and self.db.row.get(key) == value
            else:
                matches = matches and value == "null" and self.db.row.get(key) is None
        if not matches:
            return _CasResult([])
        if self.operation == "update":
            self.db.row.update(self.payload)
        return _CasResult([dict(self.db.row)])


class _CasDb:
    def __init__(self, row):
        self.row = dict(row) if row is not None else None

    def table(self, name):
        assert name == "scribe_v2_cache_entries"
        return _CasQuery(self)


def test_late_owner_failure_cannot_overwrite_completed_cache():
    db = _CasDb({"cache_key": "cache-1", "status": "running", "owner_token": "owner-old"})

    completed = job_runner.scribe_v2_cache.mark_cache_completed(
        db,
        cache_key="cache-1",
        owner_token="owner-old",
        raw_json_key="attempts/old/raw.json",
        raw_srt_key="attempts/old/raw.srt",
        external_task_id="chalna-old",
    )
    late_failure = job_runner.scribe_v2_cache.mark_cache_failed(
        db,
        cache_key="cache-1",
        owner_token="owner-old",
        error_message="late failure",
        failure_kind="connection",
        retryable=True,
        resubmit_safe=False,
    )

    assert completed is True
    assert late_failure is False
    assert db.row["status"] == "completed"
    assert db.row["error_message"] is None


def test_retry_claim_rejects_old_generation_failure_and_provider_status():
    db = _CasDb({
        "cache_key": "cache-1",
        "status": "failed",
        "owner_token": "owner-old",
        "attempt_count": 1,
        "retryable": True,
        "resubmit_safe": True,
    })

    claimed = job_runner.scribe_v2_cache.claim_failed_entry_for_retry(
        db,
        cache_key="cache-1",
        owner_token="owner-new",
        expected_attempt_count=1,
    )
    late_failure = job_runner.scribe_v2_cache.mark_cache_failed(
        db,
        cache_key="cache-1",
        owner_token="owner-old",
        error_message="old owner failed",
    )
    late_status = job_runner.scribe_v2_cache.record_provider_status(
        db,
        cache_key="cache-1",
        owner_token="owner-old",
        payload={"provider_transcription_id": "old-transcript"},
    )

    assert claimed["owner_token"] == "owner-new"
    assert claimed["attempt_count"] == 2
    assert late_failure is False
    assert late_status is False
    assert db.row["status"] == "running"
    assert db.row["owner_token"] == "owner-new"
    assert db.row.get("provider_transcription_id") is None


def test_running_cache_timeout_cas_marks_recovery_required_without_resubmit():
    db = _CasDb({
        "cache_key": "cache-1",
        "status": "running",
        "owner_token": "owner-1",
        "attempt_count": 1,
        "external_task_id": None,
    })

    with pytest.raises(RuntimeError, match="requires recovery"):
        job_runner.scribe_v2_cache.wait_for_running_entry(
            db,
            cache_key="cache-1",
            timeout_seconds=-1,
            interval_seconds=0,
        )

    assert db.row["status"] == "failed"
    assert db.row["failure_kind"] == "recovery_required"
    assert db.row["retryable"] is True
    assert db.row["resubmit_safe"] is False


def test_running_cache_wait_resets_timeout_when_owner_generation_changes(monkeypatch):
    entries = iter([
        {
            "cache_key": "cache-1",
            "status": "running",
            "owner_token": "owner-1",
            "attempt_count": 1,
        },
        {
            "cache_key": "cache-1",
            "status": "running",
            "owner_token": "owner-2",
            "attempt_count": 2,
        },
        {
            "cache_key": "cache-1",
            "status": "completed",
            "owner_token": "owner-2",
            "attempt_count": 2,
        },
    ])
    monotonic_values = iter([0.0, 9.0, 18.0, 27.0])
    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "get_cache_entry",
        lambda db, cache_key: next(entries),
    )
    monkeypatch.setattr(
        job_runner.scribe_v2_cache.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(job_runner.scribe_v2_cache.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        job_runner.scribe_v2_cache,
        "mark_cache_failed",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("new generation must get a fresh timeout")),
    )

    result = job_runner.scribe_v2_cache.wait_for_running_entry(
        object(),
        cache_key="cache-1",
        timeout_seconds=10,
        interval_seconds=0,
    )

    assert result["status"] == "completed"
    assert result["owner_token"] == "owner-2"


def test_stale_owner_artifacts_are_isolated_by_owner_token(monkeypatch, tmp_path):
    raw_json = tmp_path / "source.scribe.raw.json"
    raw_srt = tmp_path / "source.scribe.raw.srt"
    raw_json.write_text("{}", encoding="utf-8")
    raw_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    db = _CasDb({
        "cache_key": "cache-1",
        "status": "running",
        "owner_token": "owner-new",
        "attempt_count": 2,
    })
    storage = {}
    monkeypatch.setattr(
        job_runner.r2,
        "upload_file",
        lambda local, key, content_type: storage.__setitem__(key, Path(local).read_bytes()),
    )
    monkeypatch.setattr(job_runner.r2, "download_to_bytes", lambda key: storage[key])

    published = job_runner._complete_raw_scribe_cache(
        db,
        cache_key="cache-1",
        result=job_runner.chalna.RawScribeResult(
            raw_json_path=str(raw_json),
            raw_srt_path=str(raw_srt),
            external_task_id="chalna-old",
        ),
        owner_token="owner-old",
        expected_status="running",
    )

    assert published is False
    assert db.row["owner_token"] == "owner-new"
    assert db.row.get("raw_json_r2_key") is None
    assert set(storage) == {
        "cache/scribe-v2/cache-1/attempts/owner-old/raw.json",
        "cache/scribe-v2/cache-1/attempts/owner-old/raw.srt",
    }
