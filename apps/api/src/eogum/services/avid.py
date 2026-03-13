"""Wrapper around avid-cli commands."""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from eogum.config import settings

logger = logging.getLogger(__name__)


def _build_avid_env() -> dict[str, str]:
    env = os.environ.copy()
    avid_bin_dir = str(settings.resolved_avid_bin.parent)
    current_path = env.get("PATH", "")
    env["PATH"] = f"{avid_bin_dir}:{current_path}" if current_path else avid_bin_dir
    env["HOME"] = env.get("HOME") or str(Path.home())
    env["CHALNA_API_URL"] = settings.chalna_url
    return env


def _run_avid(args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    """Run an avid-cli command."""
    cmd = [str(settings.resolved_avid_bin)] + args
    logger.info("Running avid-cli: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        cwd=str(settings.resolved_avid_backend_root),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_build_avid_env(),
    )

    if result.returncode != 0:
        logger.error("avid-cli stdout: %s", result.stdout[-500:] if result.stdout else "")
        logger.error("avid-cli stderr: %s", result.stderr[-500:] if result.stderr else "")
        detail = (result.stderr or result.stdout or "unknown avid-cli error")[:500]
        raise RuntimeError(f"avid-cli command failed: {detail}")

    return result


def _run_avid_json(args: list[str], timeout: int = 3600) -> dict[str, Any]:
    if "--json" not in args:
        args = [*args, "--json"]

    result = _run_avid(args, timeout=timeout)
    stdout = result.stdout.strip()

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "avid-cli did not return valid JSON. "
            f"stdout tail: {stdout[-500:] or '(empty)'}"
        ) from exc

    if payload.get("status") != "ok":
        raise RuntimeError(f"avid-cli returned non-ok status: {payload}")

    return payload


def _artifact(payload: dict[str, Any], name: str) -> str:
    artifacts = payload.get("artifacts") or {}
    value = artifacts.get(name)
    if not value:
        raise RuntimeError(f"avid-cli result missing artifact '{name}': {payload}")
    return str(value)


def version() -> dict[str, Any]:
    """Return avid version metadata."""
    return _run_avid_json(["version"], timeout=30)


def get_version() -> str | None:
    """Return the best available avid version string for audit logging."""
    try:
        payload = version()
    except Exception:
        logger.exception("Failed to read avid-cli version")
        return None

    return (
        payload.get("avid_version")
        or payload.get("git_revision")
        or payload.get("package_version")
    )


def doctor(provider: str = "claude") -> dict[str, Any]:
    """Run avid environment diagnostics."""
    return _run_avid_json(["doctor", "--provider", provider], timeout=30)


def transcribe(source_path: str, language: str = "ko", output_dir: str | None = None, context: str | None = None) -> str:
    """Run avid transcribe. Returns path to generated SRT file."""
    args = [
        "transcribe", source_path,
        "-l", language,
        "--chalna-url", settings.chalna_url,
        "--llm-refine",
    ]
    if output_dir:
        args += ["-d", output_dir]
    if context:
        args += ["--context", context]

    payload = _run_avid_json(args, timeout=7200)
    return _artifact(payload, "srt")


def transcript_overview(srt_path: str, output_path: str | None = None) -> str:
    """Run avid transcript-overview (Pass 1). Returns path to storyline.json."""
    args = ["transcript-overview", srt_path, "--provider", "claude"]
    if output_path:
        args += ["-o", output_path]

    payload = _run_avid_json(args, timeout=1800)
    return _artifact(payload, "storyline")


def subtitle_cut(
    source_path: str,
    srt_path: str,
    context_path: str | None = None,
    output_dir: str | None = None,
    final: bool = False,
    extra_sources: list[str] | None = None,
) -> dict[str, str]:
    """Run avid subtitle-cut (Pass 2). Returns result paths dict."""
    args = ["subtitle-cut", source_path, "--srt", srt_path, "--provider", "claude"]
    if context_path:
        args += ["--context", context_path]
    if output_dir:
        args += ["-d", output_dir]
    if final:
        args += ["--final"]
    for src in extra_sources or []:
        args += ["--extra-source", src]

    payload = _run_avid_json(args, timeout=1800)
    return {key: str(value) for key, value in (payload.get("artifacts") or {}).items()}


def podcast_cut(
    source_path: str,
    srt_path: str | None = None,
    context_path: str | None = None,
    output_dir: str | None = None,
    final: bool = False,
    extra_sources: list[str] | None = None,
) -> dict[str, str]:
    """Run avid podcast-cut (Pass 2). Returns result paths dict."""
    args = ["podcast-cut", source_path, "--provider", "claude"]
    if srt_path:
        args += ["--srt", srt_path]
    if context_path:
        args += ["--context", context_path]
    if output_dir:
        args += ["-d", output_dir]
    if final:
        args += ["--final"]
    for src in extra_sources or []:
        args += ["--extra-source", src]

    payload = _run_avid_json(args, timeout=1800)
    return {key: str(value) for key, value in (payload.get("artifacts") or {}).items()}


def reexport(
    project_json_path: str,
    output_dir: str,
    source_path: str | None = None,
    evaluation_path: str | None = None,
    extra_sources: list[str] | None = None,
    content_mode: str = "disabled",
) -> dict[str, Any]:
    """Re-export an avid project with optional evaluation overrides and extra sources."""
    args = [
        "reexport",
        "--project-json", project_json_path,
        "--output-dir", output_dir,
        "--content-mode", content_mode,
    ]
    if source_path:
        args += ["--source", source_path]
    if evaluation_path:
        args += ["--evaluation", evaluation_path]
    for src in extra_sources or []:
        args += ["--extra-source", src]

    return _run_avid_json(args, timeout=3600)


def apply_evaluation(
    project_json_path: str,
    evaluation_path: str,
    output_project_json: str,
) -> dict[str, Any]:
    args = [
        "apply-evaluation",
        "--project-json", project_json_path,
        "--evaluation", evaluation_path,
        "--output-project-json", output_project_json,
    ]
    return _run_avid_json(args, timeout=300)


def export_project(
    project_json_path: str,
    output_dir: str,
    output_path: str | None = None,
    silence_mode: str = "cut",
    content_mode: str = "disabled",
) -> dict[str, Any]:
    args = [
        "export-project",
        "--project-json", project_json_path,
        "--output-dir", output_dir,
        "--silence-mode", silence_mode,
        "--content-mode", content_mode,
    ]
    if output_path:
        args += ["-o", output_path]
    return _run_avid_json(args, timeout=3600)


def rebuild_multicam(
    project_json_path: str,
    source_path: str,
    extra_sources: list[str],
    output_project_json: str,
    offsets: list[int] | None = None,
) -> dict[str, Any]:
    args = [
        "rebuild-multicam",
        "--project-json", project_json_path,
        "--source", source_path,
        "--output-project-json", output_project_json,
    ]
    for src in extra_sources:
        args += ["--extra-source", src]
    for offset in offsets or []:
        args += ["--offset", str(offset)]
    return _run_avid_json(args, timeout=3600)


def clear_extra_sources(
    project_json_path: str,
    output_project_json: str,
) -> dict[str, Any]:
    args = [
        "clear-extra-sources",
        "--project-json", project_json_path,
        "--output-project-json", output_project_json,
    ]
    return _run_avid_json(args, timeout=300)
