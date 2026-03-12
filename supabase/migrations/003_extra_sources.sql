-- Add multicam extra source metadata to projects

alter table public.projects
  add column if not exists extra_sources jsonb not null default '[]'::jsonb;

comment on column public.projects.extra_sources is
  'List of extra source files for multicam re-export. Format: [{"r2_key","filename","size_bytes"}]';
