import os
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

sys.modules.setdefault("boto3", types.SimpleNamespace(client=lambda *args, **kwargs: None))
sys.modules.setdefault("botocore", types.ModuleType("botocore"))
sys.modules.setdefault(
    "botocore.config",
    types.SimpleNamespace(Config=lambda *args, **kwargs: None),
)
sys.modules.setdefault(
    "botocore.exceptions",
    types.SimpleNamespace(ClientError=Exception),
)
sys.modules.setdefault(
    "eogum.services.r2",
    types.SimpleNamespace(
        download_file=lambda *args, **kwargs: None,
        upload_file=lambda *args, **kwargs: None,
    ),
)

from eogum.services import source_derivatives  # noqa: E402


def test_source_derivative_media_info_promotes_video_timecode_for_fcpxml():
    payload = {
        "format": {"duration": "10.0"},
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "60/1",
                "avg_frame_rate": "60/1",
                "duration": "10.0",
                "nb_frames": "600",
                "tags": {"timecode": "21:01:07:00"},
            },
            {
                "codec_type": "data",
                "codec_tag_string": "tmcd",
                "tags": {"timecode": "21:01:12:00"},
            },
        ],
    }

    info = source_derivatives._normalize_media_info(payload)

    assert info["timecode"] == "21:01:07:00"
    assert info["timecode_source_kind"] == "video"
    assert info["timecode_start_seconds"] == "4540020/60"
    assert info["fcpxml_timecode_start_seconds"] == "4540020/60"


def test_source_derivative_media_info_extracts_timecode_from_rtmd_data_stream():
    payload = {
        "format": {"duration": "10.0"},
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30000/1001",
                "avg_frame_rate": "30000/1001",
                "duration": "10.0",
                "nb_frames": "300",
            },
            {
                "codec_type": "data",
                "codec_tag_string": "rtmd",
                "tags": {"timecode": "05:56:31:16"},
            },
            {"codec_type": "audio", "sample_rate": "48000", "channels": 2},
        ],
    }

    info = source_derivatives._normalize_media_info(payload)

    assert info["timecode"] == "05:56:31:16"
    assert info["timecode_rate"] == "30000/1001"
    assert info["timecode_start_frames"] == 641_746
    assert info["timecode_start_seconds"] == "642387746/30000"
    assert info["timecode_source_kind"] == "rtmd"
    assert info["fcpxml_timecode_start_seconds"] is None


def test_source_derivative_media_info_promotes_tmcd_timecode_for_fcpxml():
    payload = {
        "format": {"duration": "10.0"},
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "60/1",
                "avg_frame_rate": "60/1",
                "duration": "10.0",
                "nb_frames": "600",
            },
            {
                "codec_type": "data",
                "codec_tag_string": "tmcd",
                "tags": {"timecode": "21:01:07:00"},
            },
        ],
    }

    info = source_derivatives._normalize_media_info(payload)

    assert info["timecode"] == "21:01:07:00"
    assert info["timecode_source_kind"] == "tmcd"
    assert info["timecode_start_seconds"] == "4540020/60"
    assert info["fcpxml_timecode_start_seconds"] == "4540020/60"


def test_source_derivative_media_info_keeps_format_timecode_raw_only():
    payload = {
        "format": {"duration": "10.0", "tags": {"timecode": "01:00:00:00"}},
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30/1",
                "avg_frame_rate": "30/1",
                "duration": "10.0",
                "nb_frames": "300",
            },
        ],
    }

    info = source_derivatives._normalize_media_info(payload)

    assert info["timecode"] == "01:00:00:00"
    assert info["timecode_source_kind"] == "format"
    assert info["timecode_start_seconds"] == "108000/30"
    assert info["fcpxml_timecode_start_seconds"] is None


def test_source_derivative_readiness_requires_current_media_info_version():
    old_snapshot = {
        "status": "ready",
        "media_info_r2_key": "derived/source/media_info.json",
        "audio_proxy_r2_key": "derived/source/audio_proxy.flac",
    }
    v2_snapshot = {
        **old_snapshot,
        "media_info_version": 2,
    }
    current_snapshot = {
        **old_snapshot,
        "media_info_version": source_derivatives.MEDIA_INFO_SCHEMA_VERSION,
    }

    assert not source_derivatives.is_ready(old_snapshot)
    assert not source_derivatives.is_ready(v2_snapshot)
    assert source_derivatives.is_ready(current_snapshot)
