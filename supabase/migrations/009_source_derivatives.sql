-- Store lightweight derived assets for audio-based multicam sync.

alter table public.projects
  add column if not exists source_derived jsonb not null default '{}'::jsonb;

alter table public.source_assets
  add column if not exists derived_status text,
  add column if not exists media_info_r2_key text,
  add column if not exists audio_proxy_r2_key text,
  add column if not exists audio_codec text,
  add column if not exists sample_rate integer,
  add column if not exists channels integer,
  add column if not exists duration_ms integer,
  add column if not exists duration_diff_ms integer,
  add column if not exists media_info_version integer,
  add column if not exists derived_error text,
  add column if not exists derived_at timestamptz;

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
    'cut_decision',
    'source_derive'
  ));

create index if not exists idx_source_assets_r2_key
  on public.source_assets(r2_key);

create index if not exists idx_jobs_source_derive
  on public.jobs(project_id, type, status)
  where type = 'source_derive';

comment on column public.projects.source_derived is
  'Primary source derived asset snapshot for audio-based multicam sync.';

comment on column public.source_assets.media_info_r2_key is
  'R2 key for ffprobe-derived media_info.json used by lightweight multicam sync.';

comment on column public.source_assets.audio_proxy_r2_key is
  'R2 key for 16 kHz mono FLAC audio proxy used by lightweight multicam sync.';

comment on column public.source_assets.media_info_version is
  'Version of the derived media_info schema. Version 2 includes source timecode metadata.';
