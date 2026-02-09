"""Wrapper around avid (auto-video-edit) CLI commands."""

import json
import logging
import subprocess
from pathlib import Path

from eogum.config import settings

logger = logging.getLogger(__name__)


def _run_avid(args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    """Run an avid-cli command."""
    cmd = ["python", "-m", "avid.cli"] + args
    logger.info("Running avid: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        cwd=str(settings.avid_cli_path),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(Path.home()),
            "PYTHONPATH": str(settings.avid_cli_path / "src"),
        },
    )

    if result.returncode != 0:
        logger.error("avid failed: %s", result.stderr)
        raise RuntimeError(f"avid command failed: {result.stderr[:500]}")

    return result


def transcribe(source_path: str, language: str = "ko", output_dir: str | None = None) -> str:
    """Run avid transcribe. Returns path to generated SRT file."""
    args = ["transcribe", source_path, "-l", language]
    if output_dir:
        args += ["-d", output_dir]

    result = _run_avid(args, timeout=3600)

    # Find the generated SRT path from output
    for line in result.stdout.strip().split("\n"):
        if line.endswith(".srt"):
            return line.strip()

    # Fallback: look for SRT in output dir
    src = Path(source_path)
    srt_path = (Path(output_dir) if output_dir else src.parent) / f"{src.stem}.srt"
    if srt_path.exists():
        return str(srt_path)

    raise RuntimeError("SRT file not found after transcription")


def transcript_overview(srt_path: str, output_path: str | None = None) -> str:
    """Run avid transcript-overview (Pass 1). Returns path to storyline.json."""
    args = ["transcript-overview", srt_path]
    if output_path:
        args += ["-o", output_path]

    _run_avid(args, timeout=1800)

    # Find storyline.json
    if output_path:
        return output_path

    srt = Path(srt_path)
    storyline_path = srt.parent / f"{srt.stem}_storyline.json"
    if storyline_path.exists():
        return str(storyline_path)

    raise RuntimeError("Storyline file not found after transcript-overview")


def subtitle_cut(
    source_path: str,
    srt_path: str,
    context_path: str | None = None,
    output_dir: str | None = None,
    final: bool = False,
) -> dict:
    """Run avid subtitle-cut (Pass 2). Returns result paths dict."""
    args = ["subtitle-cut", source_path, "--srt", srt_path]
    if context_path:
        args += ["--context", context_path]
    if output_dir:
        args += ["-d", output_dir]
    if final:
        args += ["--final"]

    _run_avid(args, timeout=1800)
    return _collect_results(source_path, output_dir)


def podcast_cut(
    source_path: str,
    srt_path: str | None = None,
    context_path: str | None = None,
    output_dir: str | None = None,
    final: bool = False,
) -> dict:
    """Run avid podcast-cut (Pass 2). Returns result paths dict."""
    args = ["podcast-cut", source_path]
    if srt_path:
        args += ["--srt", srt_path]
    if context_path:
        args += ["--context", context_path]
    if output_dir:
        args += ["-d", output_dir]
    if final:
        args += ["--final"]

    _run_avid(args, timeout=1800)
    return _collect_results(source_path, output_dir)


def _collect_results(source_path: str, output_dir: str | None) -> dict:
    """Collect result file paths after processing."""
    src = Path(source_path)
    base_dir = Path(output_dir) if output_dir else src.parent
    stem = src.stem

    results = {}

    fcpxml = base_dir / f"{stem}.fcpxml"
    if fcpxml.exists():
        results["fcpxml"] = str(fcpxml)

    srt = base_dir / f"{stem}.srt"
    if srt.exists():
        results["srt"] = str(srt)

    report = base_dir / f"{stem}.report.md"
    if report.exists():
        results["report"] = str(report)

    avid_json = list(base_dir.glob(f"{stem}*.avid.json"))
    if avid_json:
        results["project_json"] = str(avid_json[0])

    return results
