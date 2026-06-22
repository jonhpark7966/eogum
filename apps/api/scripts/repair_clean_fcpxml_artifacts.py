#!/usr/bin/env python3
# Regenerate clean delivery FCPXML artifacts from stored project_json files.
#
# Run from apps/api:
#   PYTHONPATH=src .venv/bin/python scripts/repair_clean_fcpxml_artifacts.py --project-id <id>
#   PYTHONPATH=src .venv/bin/python scripts/repair_clean_fcpxml_artifacts.py --limit 50 --dry-run

from __future__ import annotations

import argparse
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eogum.services import avid, r2  # noqa: E402
from eogum.services.artifacts import ARTIFACT_JOB_TYPES, get_latest_artifact_job  # noqa: E402
from eogum.services.database import get_db  # noqa: E402

CLEAN_EXPORT_VERSION = 1


def _has_disabled_clips(fcpxml_path: Path) -> bool:
    root = ET.parse(fcpxml_path).getroot()
    return any(element.get("enabled") == "0" for element in root.iter())


def _iter_latest_artifact_jobs(db, project_id: str | None, limit: int) -> list[dict]:
    if project_id:
        job = get_latest_artifact_job(
            db,
            project_id,
            select="id, project_id, user_id, result_r2_keys, type, created_at",
        )
        return [job] if job else []

    result = (
        db.table("jobs")
        .select("id, project_id, user_id, result_r2_keys, type, created_at")
        .eq("status", "completed")
        .in_("type", ARTIFACT_JOB_TYPES)
        .order("created_at", desc=True)
        .limit(limit * 5)
        .execute()
    )
    latest_by_project: dict[str, dict] = {}
    for row in result.data or []:
        row_project_id = row.get("project_id")
        if not row_project_id or row_project_id in latest_by_project:
            continue
        latest_by_project[row_project_id] = row
        if len(latest_by_project) >= limit:
            break
    return list(latest_by_project.values())


def _clean_key(project_id: str, path: Path) -> str:
    suffix = path.suffix or ".fcpxml"
    return f"results/{project_id}/{path.stem}.clean.v{CLEAN_EXPORT_VERSION}{suffix}"


def _repair_job(job: dict, *, dry_run: bool) -> tuple[str, str]:
    project_id = job["project_id"]
    result_keys = dict(job.get("result_r2_keys") or {})
    project_json_key = result_keys.get("project_json")
    if not project_json_key:
        return project_id, "skipped: missing project_json"
    if result_keys.get("fcpxml_clean_export_version") == CLEAN_EXPORT_VERSION:
        return project_id, "skipped: already clean"

    with tempfile.TemporaryDirectory(prefix=f"eogum_clean_fcpxml_{project_id}_") as tmpdir:
        tmp_root = Path(tmpdir)
        project_json_path = tmp_root / "project.avid.json"
        r2.download_file(project_json_key, str(project_json_path))

        export_payload = avid.export_project(
            project_json_path=str(project_json_path),
            output_dir=str(tmp_root),
            silence_mode="cut",
            content_mode="cut",
        )
        artifacts = export_payload.get("artifacts") or {}
        fcpxml_path = Path(artifacts["fcpxml"])
        if _has_disabled_clips(fcpxml_path):
            raise RuntimeError(f"clean export still contains disabled clips: {fcpxml_path}")

        if dry_run:
            return project_id, f"dry-run: would upload {fcpxml_path.name}"

        fcpxml_key = _clean_key(project_id, fcpxml_path)
        r2.upload_file(str(fcpxml_path), fcpxml_key, "application/xml")
        result_keys["fcpxml"] = fcpxml_key
        result_keys["fcpxml_clean_export_version"] = CLEAN_EXPORT_VERSION

        srt_path_raw = artifacts.get("srt")
        if srt_path_raw:
            srt_path = Path(srt_path_raw)
            srt_key = _clean_key(project_id, srt_path)
            r2.upload_file(str(srt_path), srt_key, "text/plain")
            result_keys["srt"] = srt_key

    db = get_db()
    db.table("jobs").update({"result_r2_keys": result_keys}).eq("id", job["id"]).execute()
    return project_id, f"updated: {result_keys['fcpxml']}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair completed jobs with clean FCPXML exports.")
    parser.add_argument("--project-id", help="Repair only one project id")
    parser.add_argument("--limit", type=int, default=25, help="Max latest projects to scan")
    parser.add_argument("--dry-run", action="store_true", help="Do not upload or update jobs")
    args = parser.parse_args()

    db = get_db()
    jobs = _iter_latest_artifact_jobs(db, args.project_id, args.limit)
    if not jobs:
        print("no artifact jobs found")
        return 0

    failed = 0
    for job in jobs:
        try:
            project_id, status = _repair_job(job, dry_run=args.dry_run)
            print(f"[{project_id}] {status}")
        except Exception as exc:
            failed += 1
            print(f"[{job.get('project_id')}] failed: {exc}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
