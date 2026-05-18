-- Add DB-backed admin flag for backend project access control.

alter table public.profiles
  add column if not exists is_admin boolean not null default false;

comment on column public.profiles.is_admin is
  'Allows backend API admin users to access and manage projects across accounts.';

update public.profiles
set is_admin = true
where id = '12fa3f80-2fb5-49af-a53b-20e362aa21f3';
