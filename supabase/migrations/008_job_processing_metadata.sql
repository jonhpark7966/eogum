-- Track per-job processing details such as segmentation runtime path.

alter table public.jobs
  add column if not exists processing_metadata jsonb not null default '{}'::jsonb;

comment on column public.jobs.processing_metadata is
  'Per-job processing metadata, including Chalna segmentation source/mode/cache/fallback details.';
