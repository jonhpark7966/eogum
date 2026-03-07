#!/usr/bin/env python3
"""Query Supabase for projects with evaluation data and their job result_r2_keys."""

import json
import os
import sys

# Use the supabase client from the API's venv
sys.path.insert(0, "/home/jonhpark/workspace/eogum/apps/api/.venv/lib/python3.12/site-packages")

from supabase import create_client

SUPABASE_URL = "https://qacisezaacxakdzptfih.supabase.co"
SUPABASE_SERVICE_KEY = "***REMOVED***"

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# 1. List all projects
print("=" * 80)
print("ALL PROJECTS")
print("=" * 80)
projects_resp = supabase.table("projects").select("*").order("created_at", desc=True).execute()
for p in projects_resp.data:
    print(f"\n  ID:            {p['id']}")
    print(f"  Name:          {p['name']}")
    print(f"  Status:        {p['status']}")
    print(f"  Cut Type:      {p['cut_type']}")
    print(f"  Language:       {p['language']}")
    print(f"  Source R2 Key: {p.get('source_r2_key')}")
    print(f"  Source File:   {p.get('source_filename')}")
    print(f"  Duration (s):  {p.get('source_duration_seconds')}")
    print(f"  Created:       {p['created_at']}")

# 2. Find projects with evaluations
print("\n" + "=" * 80)
print("PROJECTS WITH EVALUATIONS")
print("=" * 80)
evals_resp = supabase.table("evaluations").select("project_id").execute()
eval_project_ids = list(set(e["project_id"] for e in evals_resp.data))
print(f"  Projects with evaluations: {eval_project_ids}")

# 3. For each project with evaluations (or fallback to most recent completed), get jobs
target_project_ids = eval_project_ids
if not target_project_ids:
    # Fallback: most recent completed project
    completed = supabase.table("projects").select("id").eq("status", "completed").order("created_at", desc=True).limit(1).execute()
    if completed.data:
        target_project_ids = [completed.data[0]["id"]]
        print("  (No evaluations found, using most recent completed project)")

for pid in target_project_ids:
    print(f"\n{'~' * 60}")
    # Get project details
    proj = supabase.table("projects").select("*").eq("id", pid).single().execute()
    p = proj.data
    print(f"  PROJECT: {p['name']}")
    print(f"  ID:            {p['id']}")
    print(f"  Status:        {p['status']}")
    print(f"  Cut Type:      {p['cut_type']}")
    print(f"  Source R2 Key: {p.get('source_r2_key')}")
    print(f"  Source File:   {p.get('source_filename')}")
    print(f"  Duration (s):  {p.get('source_duration_seconds')}")
    print(f"  Settings:      {json.dumps(p.get('settings'), indent=4, ensure_ascii=False)}")

    # Get all jobs for this project
    print(f"\n  JOBS:")
    jobs_resp = supabase.table("jobs").select("*").eq("project_id", pid).order("created_at").execute()
    for j in jobs_resp.data:
        print(f"\n    Job ID:        {j['id']}")
        print(f"    Type:          {j['type']}")
        print(f"    Status:        {j['status']}")
        print(f"    Progress:      {j['progress']}%")
        print(f"    Started:       {j.get('started_at')}")
        print(f"    Completed:     {j.get('completed_at')}")
        if j.get('error_message'):
            print(f"    Error:         {j['error_message']}")
        if j.get('result_r2_keys'):
            print(f"    result_r2_keys:")
            print(json.dumps(j['result_r2_keys'], indent=6, ensure_ascii=False))

    # Get evaluation details
    eval_detail = supabase.table("evaluations").select("*").eq("project_id", pid).execute()
    if eval_detail.data:
        print(f"\n  EVALUATIONS:")
        for ev in eval_detail.data:
            print(f"    Eval ID:       {ev['id']}")
            print(f"    Version:       {ev['version']}")
            print(f"    AVID Version:  {ev.get('avid_version')}")
            print(f"    Segments count: {len(ev.get('segments', []))}")

    # Get edit report
    report_resp = supabase.table("edit_reports").select("*").eq("project_id", pid).execute()
    if report_resp.data:
        print(f"\n  EDIT REPORT:")
        r = report_resp.data[0]
        print(f"    Total Duration:  {r['total_duration_seconds']}s")
        print(f"    Cut Duration:    {r['cut_duration_seconds']}s")
        print(f"    Cut Percentage:  {r['cut_percentage']}%")

# 4. Also list all jobs with result_r2_keys (any project)
print("\n" + "=" * 80)
print("ALL JOBS WITH result_r2_keys")
print("=" * 80)
all_jobs = supabase.table("jobs").select("id, project_id, type, status, result_r2_keys").not_.is_("result_r2_keys", "null").execute()
for j in all_jobs.data:
    print(f"\n  Job ID:        {j['id']}")
    print(f"  Project ID:    {j['project_id']}")
    print(f"  Type:          {j['type']}")
    print(f"  Status:        {j['status']}")
    print(f"  result_r2_keys:")
    print(json.dumps(j['result_r2_keys'], indent=4, ensure_ascii=False))
