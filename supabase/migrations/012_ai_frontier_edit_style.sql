-- Add AI Frontier as a podcast-pipeline edit style.

alter table public.projects
  drop constraint if exists projects_cut_type_check;

alter table public.projects
  add constraint projects_cut_type_check
  check (cut_type in (
    'subtitle_cut',
    'podcast_cut',
    'ai_frontier_cut'
  ));

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
    'source_derive'
  ));
