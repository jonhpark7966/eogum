-- Preserve provider identities for safe Scribe recovery and retain retry history.

alter table public.scribe_v2_cache_entries
  add column if not exists provider_request_id text,
  add column if not exists provider_transcription_id text,
  add column if not exists provider_trace_id text,
  add column if not exists owner_token uuid,
  add column if not exists failure_kind text,
  add column if not exists retryable boolean not null default false,
  add column if not exists resubmit_safe boolean not null default false,
  add column if not exists attempt_count integer not null default 0,
  add column if not exists last_attempt_at timestamptz;

alter table public.scribe_v2_cache_entries
  drop constraint if exists scribe_v2_cache_entries_attempt_count_check;

alter table public.scribe_v2_cache_entries
  add constraint scribe_v2_cache_entries_attempt_count_check
  check (attempt_count >= 0);

alter table public.jobs
  add column if not exists retry_of_job_id uuid references public.jobs(id) on delete set null,
  add column if not exists attempt_number integer not null default 1;

alter table public.jobs
  drop constraint if exists jobs_attempt_number_check;

alter table public.jobs
  add constraint jobs_attempt_number_check
  check (attempt_number >= 1);

create index if not exists idx_scribe_v2_cache_provider_transcription
  on public.scribe_v2_cache_entries(provider_transcription_id)
  where provider_transcription_id is not null;

create index if not exists idx_jobs_retry_of_job
  on public.jobs(retry_of_job_id)
  where retry_of_job_id is not null;

-- The retry flow intentionally persists a pending job before activating the
-- project. This index makes that job-first handoff concurrency-safe while still
-- allowing source-derive, cut-decision, and preview jobs to use their own lanes.
create unique index if not exists idx_jobs_one_active_initial_per_project
  on public.jobs(project_id)
  where status in ('queued', 'pending', 'running')
    and type in ('subtitle_cut', 'podcast_cut', 'ai_frontier_cut');

comment on column public.scribe_v2_cache_entries.resubmit_safe is
  'True only when Chalna has confirmed that another provider POST cannot duplicate accepted work.';

comment on column public.scribe_v2_cache_entries.owner_token is
  'Generation token used as a compare-and-set guard for all running cache owner callbacks.';

comment on column public.jobs.retry_of_job_id is
  'Previous initial project job that this explicit user retry follows.';

comment on column public.jobs.attempt_number is
  'One-based initial project processing attempt number; prior jobs remain as history.';
