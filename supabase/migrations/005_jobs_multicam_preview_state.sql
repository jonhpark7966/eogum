-- Allow reprocess/final-preview jobs and track multicam application state.

alter table public.jobs
  add column if not exists input_payload jsonb not null default '{}'::jsonb;

alter table public.projects
  add column if not exists multicam_state jsonb not null default '{}'::jsonb;

alter table public.jobs
  drop constraint if exists jobs_type_check;

alter table public.jobs
  add constraint jobs_type_check
  check (type in (
    'transcribe',
    'transcript_overview',
    'subtitle_cut',
    'podcast_cut',
    'reprocess_multicam',
    'final_preview'
  ));

alter table public.jobs
  drop constraint if exists jobs_status_check;

alter table public.jobs
  add constraint jobs_status_check
  check (status in (
    'queued',
    'pending',
    'running',
    'completed',
    'failed',
    'cancel_requested',
    'canceled'
  ));

alter table public.projects
  drop constraint if exists projects_status_check;

alter table public.projects
  add constraint projects_status_check
  check (status in (
    'created',
    'uploading',
    'queued',
    'processing',
    'completed',
    'failed',
    'reprocess_failed'
  ));

comment on column public.projects.multicam_state is
  'Multicam application lifecycle and source hash metadata.';

comment on column public.jobs.input_payload is
  'Job-specific request payload, e.g. final preview evaluation segments.';
