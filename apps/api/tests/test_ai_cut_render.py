import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from eogum.auth import CurrentUser  # noqa: E402
from eogum.routes import projects, renders  # noqa: E402
from eogum.services import ai_cut_render, job_runner, media_render  # noqa: E402


NOW = "2026-07-13T00:00:00+00:00"


def test_render_dedupe_is_canonical_and_changes_with_source_job():
    project = {"source_sha256": "abc"}
    source = {
        "id": "job-1",
        "result_r2_keys": {"srt": "ignored", "project_json": "project.json"},
    }
    reordered = {
        "result_r2_keys": {"project_json": "project.json", "srt": "ignored"},
        "id": "job-1",
    }

    assert ai_cut_render.render_dedupe_key(project, source) == ai_cut_render.render_dedupe_key(project, reordered)
    assert ai_cut_render.render_dedupe_key(project, source) != ai_cut_render.render_dedupe_key(
        project,
        {**source, "id": "job-2"},
    )


class _Query:
    def __init__(self, db, table_name: str):
        self.db = db
        self.table_name = table_name
        self.operation = "select"
        self.values = None
        self.eq_filters = {}
        self.in_filters = {}
        self.limit_value = None
        self.order_column = None
        self.order_desc = False
        self.single_value = False

    def select(self, _columns: str):
        return self

    def insert(self, values: dict):
        self.operation = "insert"
        self.values = values
        return self

    def update(self, values: dict):
        self.operation = "update"
        self.values = values
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def eq(self, column: str, value):
        self.eq_filters[column] = value
        return self

    def in_(self, column: str, values):
        self.in_filters[column] = set(values)
        return self

    def order(self, column: str, desc: bool = False):
        self.order_column = column
        self.order_desc = desc
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    def single(self):
        self.single_value = True
        return self

    def maybe_single(self):
        self.single_value = True
        return self

    def execute(self):
        if self.operation == "insert":
            row = {
                "id": f"render-{len(self.db.jobs) + 1}",
                "created_at": f"2026-07-13T00:00:{len(self.db.jobs):02d}+00:00",
                "started_at": None,
                "completed_at": None,
                "error_message": None,
                "result_r2_keys": None,
                **self.values,
            }
            self.db.jobs.append(row)
            return SimpleNamespace(data=[row])

        if self.table_name == "projects":
            rows = [self.db.project]
        elif self.table_name == "jobs":
            rows = list(self.db.jobs)
        else:
            self.db.unexpected_tables.append(self.table_name)
            rows = []
        rows = [row for row in rows if self._matches(row)]
        if self.operation == "delete":
            if self.table_name == "projects" and rows:
                self.db.project = {}
            return SimpleNamespace(data=rows)
        if self.operation == "update":
            for row in rows:
                row.update(self.values or {})
            return SimpleNamespace(data=rows)
        if self.order_column:
            rows.sort(key=lambda row: row.get(self.order_column) or "", reverse=self.order_desc)
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        return SimpleNamespace(data=(rows[0] if rows else None) if self.single_value else rows)

    def _matches(self, row: dict) -> bool:
        return all(row.get(key) == value for key, value in self.eq_filters.items()) and all(
            row.get(key) in values for key, values in self.in_filters.items()
        )


class _Db:
    def __init__(self):
        self.project = {
            "id": "project-1",
            "user_id": "owner-1",
            "name": 'My / Project: "One"',
            "status": "completed",
            "source_r2_key": "sources/main.mp4",
            "source_sha256": "sha-1",
        }
        self.jobs = [
            {
                "id": "ai-1",
                "project_id": "project-1",
                "user_id": "owner-1",
                "type": "podcast_cut",
                "status": "completed",
                "result_r2_keys": {"project_json": "results/ai-1.project.json"},
                "created_at": "2026-07-13T00:00:01+00:00",
            },
            {
                "id": "human-multicam",
                "project_id": "project-1",
                "user_id": "owner-1",
                "type": "reprocess_multicam",
                "status": "completed",
                "result_r2_keys": {"project_json": "results/human.project.json"},
                "created_at": "2026-07-13T00:00:02+00:00",
            },
        ]
        self.unexpected_tables = []

    def table(self, table_name: str):
        return _Query(self, table_name)


OWNER = CurrentUser(id="owner-1", email="owner@example.com", is_admin=False)


def test_api_reuses_same_render_and_excludes_multicam_source(monkeypatch):
    db = _Db()
    enqueued = []
    monkeypatch.setattr(renders, "get_db", lambda: db)
    monkeypatch.setattr(renders.r2, "object_exists", lambda _key: True)
    monkeypatch.setattr(renders, "enqueue_ai_cut_render", lambda project_id, job_id: enqueued.append((project_id, job_id)))

    first = renders.start_ai_cut_render("project-1", OWNER)
    second = renders.start_ai_cut_render("project-1", OWNER)

    assert first.job_id == second.job_id
    assert first.source_job_id == "ai-1"
    assert enqueued == [("project-1", first.job_id)]
    assert "evaluations" not in db.unexpected_tables


def test_api_marks_old_completed_render_stale_after_cut_decision(monkeypatch):
    db = _Db()
    monkeypatch.setattr(renders, "get_db", lambda: db)
    monkeypatch.setattr(renders.r2, "object_exists", lambda _key: True)
    monkeypatch.setattr(renders, "enqueue_ai_cut_render", lambda *_args: None)
    old = renders.start_ai_cut_render("project-1", OWNER)
    render_row = next(row for row in db.jobs if row["id"] == old.job_id)
    render_row.update({
        "status": "completed",
        "progress": 100,
        "result_r2_keys": {"video": "results/old.mp4"},
        "completed_at": NOW,
    })
    db.jobs.append({
        "id": "cut-2",
        "project_id": "project-1",
        "user_id": "owner-1",
        "type": "cut_decision",
        "status": "completed",
        "result_r2_keys": {"project_json": "results/cut-2.project.json"},
        "created_at": "2026-07-13T00:00:09+00:00",
    })

    latest = renders.get_latest_ai_cut_render("project-1", OWNER)

    assert latest.current_job is None
    assert latest.has_stale_render is True


def test_failed_render_can_be_retried(monkeypatch):
    db = _Db()
    monkeypatch.setattr(renders, "get_db", lambda: db)
    monkeypatch.setattr(renders.r2, "object_exists", lambda _key: True)
    monkeypatch.setattr(renders, "enqueue_ai_cut_render", lambda *_args: None)
    first = renders.start_ai_cut_render("project-1", OWNER)
    next(row for row in db.jobs if row["id"] == first.job_id)["status"] = "failed"

    retry = renders.start_ai_cut_render("project-1", OWNER)

    assert retry.job_id != first.job_id
    assert retry.source_job_id == first.source_job_id


def test_render_access_and_download_conflicts(monkeypatch):
    db = _Db()
    monkeypatch.setattr(renders, "get_db", lambda: db)
    monkeypatch.setattr(renders.r2, "object_exists", lambda _key: True)
    monkeypatch.setattr(renders, "enqueue_ai_cut_render", lambda *_args: None)
    job = renders.start_ai_cut_render("project-1", OWNER)

    with pytest.raises(HTTPException) as incomplete:
        renders.download_ai_cut_render("project-1", job.job_id, OWNER)
    assert incomplete.value.status_code == 409

    with pytest.raises(HTTPException) as hidden:
        renders.get_latest_ai_cut_render(
            "project-1",
            CurrentUser(id="other", email=None, is_admin=False),
        )
    assert hidden.value.status_code == 404

    admin_latest = renders.get_latest_ai_cut_render(
        "project-1",
        CurrentUser(id="admin", email="admin@example.com", is_admin=True),
    )
    assert admin_latest.current_job is not None

    render_row = next(row for row in db.jobs if row["id"] == job.job_id)
    render_row.update({
        "status": "completed",
        "result_r2_keys": {"video": "results/video.mp4"},
        "completed_at": NOW,
    })
    captured = {}
    monkeypatch.setattr(
        renders.r2,
        "generate_presigned_download",
        lambda key, filename: captured.update(key=key, filename=filename) or "https://download.example",
    )
    response = renders.download_ai_cut_render("project-1", job.job_id, OWNER)

    assert response.download_url == "https://download.example"
    assert captured == {"key": "results/video.mp4", "filename": 'My _ Project_ _One_AI-cut.mp4'}


def test_project_delete_cleans_render_mp4(monkeypatch):
    db = _Db()
    db.project["extra_sources"] = []
    db.jobs.append({
        "id": "render-completed",
        "project_id": "project-1",
        "type": "ai_cut_render",
        "status": "completed",
        "result_r2_keys": {"video": "results/render.mp4"},
        "processing_metadata": {"output_r2_key": "results/render.mp4"},
        "created_at": NOW,
    })
    deleted_keys = []
    monkeypatch.setattr(projects, "get_db", lambda: db)
    monkeypatch.setattr(projects, "_source_r2_key_is_shared", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(projects, "delete_objects", lambda keys: deleted_keys.extend(keys))

    projects.delete_project("project-1", OWNER)

    assert db.project == {}
    assert "sources/main.mp4" in deleted_keys
    assert "results/render.mp4" in deleted_keys


def test_ai_cut_migration_has_partial_dedupe_index():
    sql = (ROOT.parent.parent / "supabase" / "migrations" / "014_ai_cut_render_jobs.sql").read_text()
    assert "'ai_cut_render'" in sql
    assert "source_job_id uuid references public.jobs(id)" in sql
    assert "on public.jobs(project_id, type, dedupe_key)" in sql
    assert "status not in ('failed', 'canceled')" in sql


def test_ai_cut_and_final_preview_share_single_cpu_lane(monkeypatch):
    monkeypatch.setattr(job_runner.settings, "final_preview_worker_count", 4)
    assert job_runner._lane_for_kind("final_preview") == "final_preview"
    assert job_runner._lane_for_kind("ai_cut_render") == "final_preview"
    assert job_runner._lane_worker_limit("final_preview") == 1


def test_atomic_claim_skips_an_already_running_render(monkeypatch, tmp_path: Path):
    db = _Db()
    db.jobs.append({
        "id": "render-running",
        "project_id": "project-1",
        "user_id": "owner-1",
        "type": "ai_cut_render",
        "status": "running",
        "progress": 25,
        "source_job_id": "ai-1",
        "dedupe_key": "dedupe",
        "processing_metadata": {},
        "created_at": NOW,
    })
    monkeypatch.setattr(job_runner, "get_db", lambda: db)
    monkeypatch.setattr(job_runner.settings, "avid_temp_dir", tmp_path)
    monkeypatch.setattr(
        job_runner.media_render,
        "render_intervals",
        lambda *_args, **_kwargs: pytest.fail("duplicate worker must not enter FFmpeg"),
    )

    job_runner._render_ai_cut("project-1", "render-running")

    assert next(row for row in db.jobs if row["id"] == "render-running")["status"] == "running"


def test_ai_cut_render_uses_review_timeline_planner(monkeypatch, tmp_path: Path):
    db = _Db()
    source_job = next(row for row in db.jobs if row["id"] == "ai-1")
    dedupe_key = ai_cut_render.render_dedupe_key(db.project, source_job)
    db.jobs.append({
        "id": "render-pending",
        "project_id": "project-1",
        "user_id": "owner-1",
        "type": "ai_cut_render",
        "status": "pending",
        "progress": 0,
        "source_job_id": "ai-1",
        "dedupe_key": dedupe_key,
        "processing_metadata": {},
        "result_r2_keys": None,
        "created_at": NOW,
    })
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    project_payload = {"timeline": "from-ai-project-json"}
    planned_intervals = [(1.0, 2.0), (4.0, 1.0)]
    captured = {}

    monkeypatch.setattr(job_runner, "get_db", lambda: db)
    monkeypatch.setattr(job_runner.settings, "avid_temp_dir", tmp_path)
    monkeypatch.setattr(job_runner.r2, "download_to_bytes", lambda _key: json.dumps(project_payload).encode())
    monkeypatch.setattr(job_runner, "_get_cached_source_video", lambda *_args: source_path)
    monkeypatch.setattr(
        job_runner.media_render,
        "probe_media",
        lambda _path: {
            "duration_ms": 6000,
            "video_bitrate": 800_000,
            "video_bitrate_estimated": False,
        },
    )

    def plan_intervals(path: Path):
        captured["project_payload"] = json.loads(path.read_text())
        return planned_intervals

    monkeypatch.setattr(job_runner, "_review_timeline_intervals_from_project_json", plan_intervals)

    def render_intervals(_source, intervals, output, **kwargs):
        captured["intervals"] = intervals
        captured["profile"] = kwargs["profile"]
        output.write_bytes(b"rendered")
        return {"target_video_bitrate": 800_000}

    monkeypatch.setattr(job_runner.media_render, "render_intervals", render_intervals)
    monkeypatch.setattr(
        job_runner.media_render,
        "validate_output",
        lambda *_args, **_kwargs: {
            "duration_ms": 3000,
            "size_bytes": 8,
            "video_codec": "h264",
            "audio_codec": "aac",
            "audio_channels": 2,
            "width": 320,
            "height": 180,
            "fps": 24.0,
            "av_sync_diff_ms": 0,
            "target_video_bitrate": 800_000,
            "video_bitrate": 800_000,
            "video_bitrate_delta_percent": 0.0,
        },
    )
    monkeypatch.setattr(
        job_runner.r2,
        "upload_file",
        lambda path, key, content_type: captured.update(upload=(path, key, content_type)),
    )

    job_runner._render_ai_cut("project-1", "render-pending")

    render_job = next(row for row in db.jobs if row["id"] == "render-pending")
    assert render_job["status"] == "completed"
    assert captured["project_payload"] == project_payload
    assert captured["intervals"] == planned_intervals
    assert captured["profile"] == media_render.WEB_1080P_PROFILE
    assert captured["upload"][2] == "video/mp4"


def test_recovery_completes_a_verified_deterministic_upload(monkeypatch):
    db = _Db()
    job = {
        "id": "render-recovered",
        "project_id": "project-1",
        "type": "ai_cut_render",
        "status": "running",
        "processing_metadata": {
            "output_r2_key": "results/project-1/renders/key/main-source-ai-cut.mp4",
            "size_bytes": 1234,
            "duration_ms": 5000,
        },
    }
    db.jobs.append(job)
    monkeypatch.setattr(job_runner.r2, "head_object", lambda _key: {"size_bytes": 1234})

    assert job_runner._complete_recovered_ai_cut_upload(db, job) is True
    assert job["status"] == "completed"
    assert job["progress"] == 100
    assert job["result_r2_keys"] == {
        "video": "results/project-1/renders/key/main-source-ai-cut.mp4",
    }


def test_restart_recovery_requeues_pending_and_running_jobs(monkeypatch):
    db = _Db()
    db.jobs.extend([
        {
            "id": "render-pending",
            "project_id": "project-1",
            "type": "ai_cut_render",
            "status": "pending",
            "created_at": NOW,
            "started_at": None,
        },
        {
            "id": "render-running",
            "project_id": "project-1",
            "type": "ai_cut_render",
            "status": "running",
            "created_at": NOW,
            "started_at": NOW,
        },
    ])
    enqueued = []
    monkeypatch.setattr(job_runner, "get_db", lambda: db)
    monkeypatch.setattr(
        job_runner,
        "enqueue_ai_cut_render",
        lambda project_id, job_id: enqueued.append((project_id, job_id)),
    )

    recovered = job_runner.recover_stuck_ai_cut_renders(recover_running=True)

    assert recovered == 2
    assert enqueued == [
        ("project-1", "render-pending"),
        ("project-1", "render-running"),
    ]
    assert next(row for row in db.jobs if row["id"] == "render-running")["status"] == "pending"


def _make_fixture(
    path: Path,
    *,
    with_audio: bool,
    duration: int = 6,
    size: str = "320x180",
) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=duration={duration}:size={size}:rate=24",
    ]
    if with_audio:
        command += [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}:sample_rate=48000",
        ]
    command += [
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-b:v",
        "800k",
        "-minrate",
        "800k",
        "-maxrate",
        "800k",
        "-bufsize",
        "1600k",
        "-x264-params",
        "nal-hrd=cbr:force-cfr=0",
        "-pix_fmt",
        "yuv420p",
    ]
    if with_audio:
        command += ["-c:a", "aac", "-shortest"]
    else:
        command += ["-an"]
    subprocess.run(command + [str(path)], check=True, capture_output=True)


@pytest.mark.parametrize("with_audio", [True, False])
def test_web_1080p_ffmpeg_render_profile(tmp_path: Path, with_audio: bool):
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    _make_fixture(source, with_audio=with_audio)

    source_metadata = media_render.probe_media(source)
    manifest = media_render.render_intervals(
        source,
        [(0.0, 1.5), (2.5, 1.5)],
        output,
        profile=media_render.WEB_1080P_PROFILE,
    )
    metadata = media_render.validate_output(
        output,
        profile=media_render.WEB_1080P_PROFILE,
        expected_duration_ms=3000,
        interval_count=2,
        expected_video_bitrate=source_metadata["video_bitrate"],
    )

    assert metadata["video_codec"] == "h264"
    assert metadata["audio_codec"] == ("aac" if with_audio else None)
    assert metadata["audio_channels"] == (2 if with_audio else None)
    assert (metadata["width"], metadata["height"]) == (320, 180)
    assert metadata["fps"] == pytest.approx(24.0)
    assert metadata["duration_ms"] == pytest.approx(3000, abs=500)
    assert manifest["target_video_bitrate"] == source_metadata["video_bitrate"]
    assert metadata["video_bitrate"] == pytest.approx(source_metadata["video_bitrate"], rel=0.10)
    assert metadata["target_video_bitrate"] == source_metadata["video_bitrate"]
    assert abs(metadata["video_bitrate_delta_percent"]) <= 10
    if metadata["av_sync_diff_ms"] is not None:
        assert metadata["av_sync_diff_ms"] <= 200


def test_web_profile_downscales_to_1080p(tmp_path: Path):
    source = tmp_path / "source-2k.mp4"
    output = tmp_path / "output-1080p.mp4"
    _make_fixture(source, with_audio=False, duration=1, size="2048x1152")

    media_render.render_intervals(
        source,
        [(0.0, 1.0)],
        output,
        profile=media_render.WEB_1080P_PROFILE,
    )
    metadata = media_render.validate_output(
        output,
        profile=media_render.WEB_1080P_PROFILE,
        expected_duration_ms=1000,
    )

    assert (metadata["width"], metadata["height"]) == (1920, 1080)


def test_web_profile_uses_source_bitrate_instead_of_crf():
    args = media_render._encoding_args(
        media_render.WEB_1080P_PROFILE,
        has_audio=True,
        target_video_bitrate=7_996_392,
    )

    assert "-crf" not in args
    assert args[args.index("-b:v") + 1] == "7996392"
    assert args[args.index("-minrate") + 1] == "7996392"
    assert args[args.index("-maxrate") + 1] == "7996392"
    assert args[args.index("-bufsize") + 1] == "15992784"
    assert args[args.index("-x264-params") + 1] == "nal-hrd=cbr:force-cfr=0"


def test_probe_media_estimates_missing_video_bitrate(monkeypatch, tmp_path: Path):
    source = tmp_path / "missing-stream-bitrate.mp4"
    source.write_bytes(b"x" * 1_250_000)
    payload = {
        "format": {"duration": "10.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "duration": "10.0",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30/1",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "duration": "10.0",
                "channels": 2,
                "bit_rate": "128000",
            },
        ],
    }
    monkeypatch.setattr(
        media_render,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )

    metadata = media_render.probe_media(source)

    assert metadata["overall_bitrate"] == 1_000_000
    assert metadata["audio_bitrate"] == 128_000
    assert metadata["video_bitrate"] == 872_000
    assert metadata["video_bitrate_estimated"] is True


def test_web_profile_rejects_video_bitrate_drift(monkeypatch, tmp_path: Path):
    output = tmp_path / "drifted.mp4"
    output.write_bytes(b"video")
    monkeypatch.setattr(
        media_render,
        "probe_media",
        lambda _path: {
            "size_bytes": output.stat().st_size,
            "video_codec": "h264",
            "audio_codec": "aac",
            "audio_channels": 2,
            "av_sync_diff_ms": 0,
            "width": 1280,
            "height": 720,
            "video_bitrate": 700_000,
            "duration_ms": 5000,
        },
    )

    with pytest.raises(RuntimeError, match="video bitrate differs from source target"):
        media_render.validate_output(
            output,
            profile=media_render.WEB_1080P_PROFILE,
            expected_video_bitrate=8_000_000,
        )


def test_ai_cut_bitrate_policy_has_a_new_render_identity():
    assert ai_cut_render.WEB_RENDER_PROFILE == media_render.WEB_1080P_PROFILE == "web_1080p_v2"
    assert ai_cut_render.RENDER_VERSION == 3
