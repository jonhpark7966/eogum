import json
from pathlib import Path

import pytest

from eogum.services import overlap_protection


def _patch_artifact_dependencies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[tuple[Path, Path]]:
    extracted: list[tuple[Path, Path]] = []

    def fake_extract_audio(source: Path, wav_path: Path) -> None:
        extracted.append((source, wav_path))
        wav_path.write_bytes(b"wav")

    monkeypatch.setattr(overlap_protection.settings, "huggingface_cache_dir", tmp_path / "hf-cache")
    monkeypatch.setattr(overlap_protection, "_extract_audio", fake_extract_audio)
    monkeypatch.setattr(overlap_protection, "_ffprobe_duration_ms", lambda _path: 4200)
    monkeypatch.setattr(overlap_protection, "_environment_payload", lambda: {"python": "test"})
    return extracted


def test_build_overlap_protection_artifact_uses_community1_only(monkeypatch, tmp_path):
    extracted = _patch_artifact_dependencies(monkeypatch, tmp_path)
    detector_calls: list[tuple[Path, Path]] = []

    def fake_community1_detector(wav_path: Path, cache_dir: Path) -> list[dict]:
        detector_calls.append((wav_path, cache_dir))
        return [
            {
                "start_ms": 1000,
                "end_ms": 1500,
                "start": 1.0,
                "end": 1.5,
                "duration_ms": 500,
                "speakers": ["SPEAKER_00", "SPEAKER_01"],
            }
        ]

    monkeypatch.setattr(overlap_protection, "_run_community1_detector", fake_community1_detector)

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    artifact_path, payload = overlap_protection.build_overlap_protection_artifact(
        source_path,
        tmp_path / "overlap",
    )

    assert not hasattr(overlap_protection, "_run_osd_detector")
    assert extracted == [(source_path, tmp_path / "overlap" / "source.overlap.16k_mono.wav")]
    assert detector_calls == [(tmp_path / "overlap" / "source.overlap.16k_mono.wav", tmp_path / "hf-cache")]
    assert artifact_path.exists()
    assert payload["status"] == "complete"
    assert list(payload["models"]) == ["community1"]
    assert payload["models"]["community1"]["status"] == "succeeded"
    assert payload["models"]["community1"]["model"] == overlap_protection.DIARIZATION_MODEL_ID
    assert payload["interval_count"] == 1
    assert payload["total_overlap_ms"] == 500
    assert payload["intervals"][0]["models"] == ["community1"]
    assert payload["intervals"][0]["speakers"] == ["SPEAKER_00", "SPEAKER_01"]

    persisted = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "complete"
    assert list(persisted["models"]) == ["community1"]


def test_build_overlap_protection_artifact_failure_records_community1_error(monkeypatch, tmp_path):
    _patch_artifact_dependencies(monkeypatch, tmp_path)

    def fail_community1_detector(_wav_path: Path, _cache_dir: Path) -> list[dict]:
        raise RuntimeError("community detector unavailable")

    monkeypatch.setattr(overlap_protection, "_run_community1_detector", fail_community1_detector)

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    output_dir = tmp_path / "overlap"

    with pytest.raises(overlap_protection.OverlapProtectionError) as exc:
        overlap_protection.build_overlap_protection_artifact(source_path, output_dir)

    payload = exc.value.payload
    assert payload["status"] == "failed"
    assert payload["interval_count"] == 0
    assert payload["total_overlap_ms"] == 0
    assert list(payload["models"]) == ["community1"]
    assert payload["models"]["community1"]["status"] == "failed"
    assert payload["models"]["community1"]["error_type"] == "RuntimeError"
    assert payload["models"]["community1"]["error"] == "community detector unavailable"

    persisted = json.loads((output_dir / "overlap_protection.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert list(persisted["models"]) == ["community1"]
