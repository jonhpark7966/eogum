-- Global source and Scribe V2 raw response cache.

create extension if not exists "pgcrypto" with schema extensions;

alter table public.projects
  add column if not exists source_sha256 text;

create table if not exists public.source_assets (
  id uuid primary key default gen_random_uuid(),
  sha256 text not null,
  size_bytes bigint not null,
  r2_key text not null,
  filename text,
  duration_seconds integer,
  created_at timestamptz not null default now(),
  last_used_at timestamptz,
  unique (sha256, size_bytes)
);

create table if not exists public.scribe_v2_cache_entries (
  id uuid primary key default gen_random_uuid(),
  cache_key text not null unique,
  source_sha256 text not null,
  source_size_bytes bigint not null,
  language text not null,
  diarize boolean not null,
  num_speakers integer,
  tag_audio_events boolean not null,
  scribe_api_version text not null default 'scribe_v2',
  status text not null default 'running'
    check (status in ('running', 'completed', 'failed')),
  raw_json_r2_key text,
  raw_srt_r2_key text,
  external_task_id text,
  error_message text,
  hit_count integer not null default 0,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  last_used_at timestamptz
);

create index if not exists idx_source_assets_sha_size
  on public.source_assets(sha256, size_bytes);

create index if not exists idx_scribe_v2_cache_source
  on public.scribe_v2_cache_entries(source_sha256, source_size_bytes);

comment on column public.projects.source_sha256 is
  'SHA-256 digest of the original source file bytes for global source reuse and Scribe cache lookup.';

comment on table public.source_assets is
  'Global source file registry keyed by file SHA-256 and byte size.';

comment on table public.scribe_v2_cache_entries is
  'Global raw ElevenLabs Scribe V2 cache keyed only by source identity and Scribe request parameters.';
