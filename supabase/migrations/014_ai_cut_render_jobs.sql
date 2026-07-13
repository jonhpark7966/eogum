-- Add durable, deduplicated AI-only main-source render jobs.

alter table public.jobs
  add column if not exists source_job_id uuid references public.jobs(id) on delete set null,
  add column if not exists dedupe_key text;

alter table public.jobs
  drop constraint if exists jobs_type_check;

alter table public.jobs
  add constraint jobs_type_check
  check (type in (
    'transcribe',
    'transcript_overview',
    'subtitle_cut',
    'podcast_cut',
    'ai_frontier_cut',
    'reprocess_multicam',
    'final_preview',
    'cut_decision',
    'source_derive',
    'ai_cut_render'
  ));

create index if not exists idx_jobs_source_job
  on public.jobs(source_job_id)
  where source_job_id is not null;

create unique index if not exists idx_jobs_ai_cut_render_dedupe
  on public.jobs(project_id, type, dedupe_key)
  where dedupe_key is not null
    and status not in ('failed', 'canceled');

comment on column public.jobs.source_job_id is
  'Completed AI artifact job whose project JSON is the immutable input for this derived job.';

comment on column public.jobs.dedupe_key is
  'SHA-256 identity for a deterministic derived job input and render version.';
