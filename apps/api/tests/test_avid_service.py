from __future__ import annotations

from eogum.services import avid


def test_apply_evaluation_uses_split_command(monkeypatch):
    recorded: dict[str, object] = {}

    def fake_run(args: list[str], timeout: int = 3600):
        recorded["args"] = args
        recorded["timeout"] = timeout
        return {"status": "ok"}

    monkeypatch.setattr(avid, "_run_avid_json", fake_run)

    payload = avid.apply_evaluation(
        project_json_path="/tmp/in.project.avid.json",
        evaluation_path="/tmp/evaluation.json",
        output_project_json="/tmp/out.project.avid.json",
    )

    assert payload == {"status": "ok"}
    assert recorded["args"] == [
        "apply-evaluation",
        "--project-json", "/tmp/in.project.avid.json",
        "--evaluation", "/tmp/evaluation.json",
        "--output-project-json", "/tmp/out.project.avid.json",
    ]
    assert recorded["timeout"] == 300


def test_export_project_uses_split_command(monkeypatch):
    recorded: dict[str, object] = {}

    def fake_run(args: list[str], timeout: int = 3600):
        recorded["args"] = args
        recorded["timeout"] = timeout
        return {"status": "ok"}

    monkeypatch.setattr(avid, "_run_avid_json", fake_run)

    payload = avid.export_project(
        project_json_path="/tmp/in.project.avid.json",
        output_dir="/tmp/output",
        content_mode="cut",
    )

    assert payload == {"status": "ok"}
    assert recorded["args"] == [
        "export-project",
        "--project-json", "/tmp/in.project.avid.json",
        "--output-dir", "/tmp/output",
        "--silence-mode", "cut",
        "--content-mode", "cut",
    ]
    assert recorded["timeout"] == 3600


def test_rebuild_multicam_passes_offsets(monkeypatch):
    recorded: dict[str, object] = {}

    def fake_run(args: list[str], timeout: int = 3600):
        recorded["args"] = args
        recorded["timeout"] = timeout
        return {"status": "ok"}

    monkeypatch.setattr(avid, "_run_avid_json", fake_run)

    payload = avid.rebuild_multicam(
        project_json_path="/tmp/in.project.avid.json",
        source_path="/tmp/main.mp4",
        extra_sources=["/tmp/cam2.mp4", "/tmp/cam3.mp4"],
        output_project_json="/tmp/out.project.avid.json",
        offsets=[1200, -300],
    )

    assert payload == {"status": "ok"}
    assert recorded["args"] == [
        "rebuild-multicam",
        "--project-json", "/tmp/in.project.avid.json",
        "--source", "/tmp/main.mp4",
        "--output-project-json", "/tmp/out.project.avid.json",
        "--extra-source", "/tmp/cam2.mp4",
        "--extra-source", "/tmp/cam3.mp4",
        "--offset", "1200",
        "--offset", "-300",
    ]
    assert recorded["timeout"] == 3600


def test_clear_extra_sources_uses_split_command(monkeypatch):
    recorded: dict[str, object] = {}

    def fake_run(args: list[str], timeout: int = 3600):
        recorded["args"] = args
        recorded["timeout"] = timeout
        return {"status": "ok"}

    monkeypatch.setattr(avid, "_run_avid_json", fake_run)

    payload = avid.clear_extra_sources(
        project_json_path="/tmp/in.project.avid.json",
        output_project_json="/tmp/out.project.avid.json",
    )

    assert payload == {"status": "ok"}
    assert recorded["args"] == [
        "clear-extra-sources",
        "--project-json", "/tmp/in.project.avid.json",
        "--output-project-json", "/tmp/out.project.avid.json",
    ]
    assert recorded["timeout"] == 300
