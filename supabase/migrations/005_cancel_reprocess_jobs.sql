-- Allow multicam reprocess jobs to be cancelled by the user.

alter table public.jobs drop constraint jobs_status_check;
alter table public.jobs add constraint jobs_status_check
  check (status in ('queued', 'pending', 'running', 'completed', 'failed', 'canceled'));
