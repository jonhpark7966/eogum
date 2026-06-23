-- Track derived media_info schema version for lazy refresh.

alter table public.source_assets
  add column if not exists media_info_version integer;

comment on column public.source_assets.media_info_version is
  'Version of the derived media_info schema. Version 2 includes source timecode metadata.';
