-- Add reprocess_multicam support to check constraints

-- Jobs: allow 'reprocess_multicam' type and 'pending' status
alter table public.jobs drop constraint jobs_type_check;
alter table public.jobs add constraint jobs_type_check
  check (type in ('transcribe', 'transcript_overview', 'subtitle_cut', 'podcast_cut', 'reprocess_multicam'));

alter table public.jobs drop constraint jobs_status_check;
alter table public.jobs add constraint jobs_status_check
  check (status in ('queued', 'pending', 'running', 'completed', 'failed'));

-- Projects: allow 'reprocess_failed' status
alter table public.projects drop constraint projects_status_check;
alter table public.projects add constraint projects_status_check
  check (status in ('created', 'uploading', 'queued', 'processing', 'completed', 'failed', 'reprocess_failed'));
