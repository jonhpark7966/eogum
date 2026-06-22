#!/usr/bin/env python3
"""Backfill project source_sha256 and global source_assets rows.

Run from apps/api:
  PYTHONPATH=src .venv/bin/python scripts/backfill_source_assets.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eogum.services import r2, source_cache  # noqa: E402
from eogum.services.database import get_db  # noqa: E402


def main() -> int:
    db = get_db()
    result = (
        db.table("projects")
        .select("id, source_r2_key, source_filename, source_duration_seconds, source_size_bytes, source_sha256")
        .execute()
    )
    projects = [row for row in (result.data or []) if row.get("source_r2_key")]
    updated = 0
    skipped = 0
    failed = 0

    with tempfile.TemporaryDirectory(prefix="eogum_source_backfill_") as tmpdir:
        tmp_root = Path(tmpdir)
        for project in projects:
            project_id = project["id"]
            try:
                suffix = Path(project.get("source_filename") or "").suffix
                local_path = tmp_root / f"{project_id}{suffix}"
                r2.download_file(project["source_r2_key"], str(local_path))
                sha256 = source_cache.sha256_file(local_path)
                size_bytes = local_path.stat().st_size

                if project.get("source_sha256") == sha256 and project.get("source_size_bytes") == size_bytes:
                    skipped += 1
                else:
                    db.table("projects").update({
                        "source_sha256": sha256,
                        "source_size_bytes": size_bytes,
                    }).eq("id", project_id).execute()
                    updated += 1

                source_cache.upsert_source_asset(
                    db,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    r2_key=project["source_r2_key"],
                    filename=project.get("source_filename"),
                    duration_seconds=project.get("source_duration_seconds"),
                )
                local_path.unlink(missing_ok=True)
            except Exception as exc:
                failed += 1
                print(f"[failed] {project_id}: {exc}")

    print(f"source asset backfill complete: updated={updated}, skipped={skipped}, failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
