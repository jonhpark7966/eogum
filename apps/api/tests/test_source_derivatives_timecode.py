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

from eogum.services import source_derivatives


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


def test_source_derivative_readiness_requires_current_media_info_version():
    old_snapshot = {
        "status": "ready",
        "media_info_r2_key": "derived/source/media_info.json",
        "audio_proxy_r2_key": "derived/source/audio_proxy.flac",
    }
    current_snapshot = {
        **old_snapshot,
        "media_info_version": source_derivatives.MEDIA_INFO_SCHEMA_VERSION,
    }

    assert not source_derivatives.is_ready(old_snapshot)
    assert source_derivatives.is_ready(current_snapshot)
