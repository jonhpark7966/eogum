import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from eogum.services import job_runner  # noqa: E402


def _write_project_json(
    tmp_path: Path,
    *,
    segments: list[dict],
    decisions: list[dict] | None = None,
    segmentation_boundary_rule: str = "low_energy_gap_v1",
    duration_ms: int = 100_000,
) -> Path:
    project = {
        "name": "preview-test",
        "source_files": [
            {
                "id": "source",
                "path": str(tmp_path / "source.mp4"),
                "original_name": "source.mp4",
                "info": {
                    "duration_ms": duration_ms,
                    "width": 1280,
                    "height": 720,
                    "fps": 23.612399758393472,
                    "video_frame_count": 135924,
                    "has_audio": True,
                },
            }
        ],
        "tracks": [
            {
                "id": "source_video",
                "source_file_id": "source",
                "track_type": "video",
                "offset_ms": 0,
            },
            {
                "id": "source_audio",
                "source_file_id": "source",
                "track_type": "audio",
                "offset_ms": 0,
            },
        ],
        "transcription": {
            "source_track_id": "source_audio",
            "language": "ko",
            "segments": segments,
        },
        "segmentation_boundary_rule": segmentation_boundary_rule,
        "edit_decisions": decisions or [],
    }
    path = tmp_path / "project.avid.json"
    path.write_text(json.dumps(project), encoding="utf-8")
    return path


def _segment(index: int, start_ms: int, end_ms: int, speaker: str = "speaker_0") -> dict:
    return {
        "index": index,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "text": f"segment {index}",
        "speaker": speaker,
    }


def _decision(
    index: int | None,
    start_ms: int,
    end_ms: int,
    *,
    edit_type: str = "mute",
    reason: str = "filler",
    active_video_track_id: str = "source_video",
) -> dict:
    return {
        "range": {"start_ms": start_ms, "end_ms": end_ms},
        "edit_type": edit_type,
        "reason": reason,
        "confidence": 1.0,
        "active_video_track_id": active_video_track_id,
        "active_audio_track_ids": ["source_audio"],
        "source_segment_index": index,
    }


def _intervals_ms(path: Path) -> list[tuple[int, int]]:
    return [
        (round(start * 1000), round((start + duration) * 1000))
        for start, duration in job_runner._final_preview_intervals_from_project_json(path)
    ]


def test_final_preview_intervals_use_source_ms_not_fcpxml_frame_time(tmp_path: Path):
    path = _write_project_json(
        tmp_path,
        segments=[
            _segment(26, 68860, 71160),
            _segment(27, 71160, 74730),
            _segment(28, 76230, 80690),
        ],
        decisions=[
            _decision(26, 68860, 71160, edit_type="mute", reason="filler"),
            _decision(None, 74730, 76230, edit_type="cut", reason="silence"),
        ],
    )

    intervals = _intervals_ms(path)

    assert (71160, 74730) in intervals
    assert (70000, 73542) not in intervals


def test_final_preview_merges_adjacent_enabled_same_speaker_segments(tmp_path: Path):
    path = _write_project_json(
        tmp_path,
        segments=[
            _segment(1, 1000, 2000, speaker="A"),
            _segment(2, 2050, 3000, speaker="A"),
            _segment(3, 3600, 3900, speaker="A"),
        ],
    )

    assert _intervals_ms(path) == [(1000, 3000), (3600, 3900)]


def test_final_preview_treats_primary_track_mutes_as_removed(tmp_path: Path):
    path = _write_project_json(
        tmp_path,
        segments=[
            _segment(1, 0, 1000),
            _segment(2, 1000, 2000),
        ],
        decisions=[
            _decision(2, 1000, 2000, edit_type="mute", reason="meta_comment"),
        ],
    )

    assert _intervals_ms(path) == [(0, 1000)]


def test_final_preview_word_boundary_alignment_matches_review_segments(tmp_path: Path):
    path = _write_project_json(
        tmp_path,
        segmentation_boundary_rule="word_boundary",
        segments=[
            _segment(1, 1000, 2000),
            _segment(2, 3000, 4000),
        ],
        decisions=[
            _decision(1, 1000, 2000, edit_type="mute", reason="filler"),
        ],
    )

    assert _intervals_ms(path) == [(2500, 4000)]
