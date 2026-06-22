-- Allow rerunning only the cut decision stage for an existing project.

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
    'final_preview',
    'cut_decision'
  ));
