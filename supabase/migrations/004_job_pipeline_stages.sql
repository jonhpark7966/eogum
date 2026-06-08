-- Store live model-stage telemetry for long-running jobs.

alter table public.jobs
add column if not exists pipeline_stages jsonb not null default '[]'::jsonb;

alter table public.jobs
add column if not exists external_task_ids jsonb not null default '{}'::jsonb;
