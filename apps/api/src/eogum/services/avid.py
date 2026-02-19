"""Wrapper around avid (auto-video-edit) CLI commands."""

import logging
import subprocess
from pathlib import Path

from eogum.config import settings

logger = logging.getLogger(__name__)

# Use avid's own venv python so avid internal subprocess calls
# (e.g. skills/subtitle-cut/main.py) use the correct interpreter
_AVID_PYTHON = str(settings.avid_cli_path / ".venv" / "bin" / "python3")

# Shared env for all avid subprocess calls
_AVID_ENV = {
    "PATH": f"{settings.avid_cli_path / '.venv/bin'}:{Path.home() / '.local/bin'}:{Path.home() / '.nvm/versions/node/v25.3.0/bin'}:/usr/local/bin:/usr/bin:/bin",
    "HOME": str(Path.home()),
    "PYTHONPATH": str(settings.avid_cli_path / "src"),
    "CHALNA_API_URL": settings.chalna_url,
}


def _run_avid(args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    """Run an avid-cli command."""
    cmd = [_AVID_PYTHON, "-m", "avid.cli"] + args
    logger.info("Running avid: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        cwd=str(settings.avid_cli_path),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_AVID_ENV,
    )

    if result.returncode != 0:
        logger.error("avid stdout: %s", result.stdout[-500:] if result.stdout else "")
        logger.error("avid stderr: %s", result.stderr[-500:] if result.stderr else "")
        raise RuntimeError(f"avid command failed: {result.stderr[:500]}")

    return result


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

    result = _run_avid(args, timeout=7200)

    # Find the generated SRT path from output
    # avid CLI prints "완료: /path/to/file.srt"
    for line in reversed(result.stdout.strip().split("\n")):
        line = line.strip()
        if "완료" in line and ".srt" in line:
            path = line.split(": ", 1)[-1].strip()
            if Path(path).exists():
                return path

    # Fallback: look for SRT in output dir
    search_dir = Path(output_dir) if output_dir else Path(source_path).parent
    src_stem = Path(source_path).stem

    # Try exact name first
    srt_path = search_dir / f"{src_stem}.srt"
    if srt_path.exists():
        return str(srt_path)

    # Try any SRT file in the directory
    srt_files = list(search_dir.glob("*.srt"))
    if srt_files:
        logger.warning("SRT not found at expected path, using: %s", srt_files[0])
        return str(srt_files[0])

    raise RuntimeError(
        f"SRT file not found after transcription. "
        f"Searched: {search_dir}/{src_stem}.srt, "
        f"stdout: {result.stdout[-300:]}"
    )


def transcript_overview(srt_path: str, output_path: str | None = None) -> str:
    """Run avid transcript-overview (Pass 1). Returns path to storyline.json."""
    args = ["transcript-overview", srt_path]
    if output_path:
        args += ["-o", output_path]

    _run_avid(args, timeout=1800)

    # If output_path was specified and exists, return it
    if output_path and Path(output_path).exists():
        return output_path

    # Fallback: search for storyline JSON near the SRT
    srt = Path(srt_path)
    for pattern in [
        f"{srt.stem}_storyline.json",
        f"{srt.stem}.storyline.json",
        "storyline.json",
    ]:
        candidate = srt.parent / pattern
        if candidate.exists():
            return str(candidate)

    raise RuntimeError(
        f"Storyline file not found after transcript-overview. "
        f"Expected: {output_path or srt.parent / f'{srt.stem}_storyline.json'}"
    )


def subtitle_cut(
    source_path: str,
    srt_path: str,
    context_path: str | None = None,
    output_dir: str | None = None,
    final: bool = False,
    extra_sources: list[str] | None = None,
) -> dict:
    """Run avid subtitle-cut (Pass 2). Returns result paths dict."""
    args = ["subtitle-cut", source_path, "--srt", srt_path]
    if context_path:
        args += ["--context", context_path]
    if output_dir:
        args += ["-d", output_dir]
    if final:
        args += ["--final"]
    for src in extra_sources or []:
        args += ["--extra-source", src]

    _run_avid(args, timeout=1800)
    return _collect_results(source_path, output_dir)


def podcast_cut(
    source_path: str,
    srt_path: str | None = None,
    context_path: str | None = None,
    output_dir: str | None = None,
    final: bool = False,
    extra_sources: list[str] | None = None,
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
    for src in extra_sources or []:
        args += ["--extra-source", src]

    _run_avid(args, timeout=1800)
    return _collect_results(source_path, output_dir)


def _collect_results(source_path: str, output_dir: str | None) -> dict:
    """Collect result file paths after processing."""
    src = Path(source_path)
    base_dir = Path(output_dir) if output_dir else src.parent
    stem = src.stem

    results = {}

    # FCPXML — may be {stem}.fcpxml or {stem}.final.fcpxml etc.
    fcpxml_files = list(base_dir.glob(f"{stem}*.fcpxml"))
    if fcpxml_files:
        results["fcpxml"] = str(fcpxml_files[0])

    # SRT — may be {stem}.srt or {stem}.final.srt etc.
    srt_files = list(base_dir.glob(f"{stem}*.srt"))
    if srt_files:
        results["srt"] = str(srt_files[0])

    # Report
    report_files = list(base_dir.glob(f"{stem}*.report.md"))
    if report_files:
        results["report"] = str(report_files[0])

    # AVID project JSON
    avid_json = list(base_dir.glob(f"{stem}*.avid.json"))
    if avid_json:
        results["project_json"] = str(avid_json[0])

    # Storyline
    storyline = base_dir / "storyline.json"
    if storyline.exists():
        results["storyline"] = str(storyline)

    logger.info("Collected results from %s: %s", base_dir, list(results.keys()))
    return results
