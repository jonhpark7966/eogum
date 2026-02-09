-- 어검 (eogum) Initial Schema
-- Supabase PostgreSQL

-- Enable UUID generation
create extension if not exists "uuid-ossp";

-- ============================================================
-- PROFILES
-- ============================================================
create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  plan text not null default 'free' check (plan in ('free', 'pro', 'enterprise')),
  created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

create policy "Users can read own profile"
  on public.profiles for select
  using (auth.uid() = id);

create policy "Users can update own profile"
  on public.profiles for update
  using (auth.uid() = id);

-- Auto-create profile on signup
create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data->>'display_name', new.email));
  return new;
end;
$$ language plpgsql security definer;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ============================================================
-- CREDITS
-- ============================================================
create table public.credits (
  user_id uuid primary key references public.profiles(id) on delete cascade,
  balance_seconds integer not null default 0,
  held_seconds integer not null default 0,
  total_granted_seconds integer not null default 0,
  updated_at timestamptz not null default now()
);

alter table public.credits enable row level security;

create policy "Users can read own credits"
  on public.credits for select
  using (auth.uid() = user_id);

-- Auto-create credits with signup bonus on profile creation
create or replace function public.handle_new_profile()
returns trigger as $$
begin
  insert into public.credits (user_id, balance_seconds, total_granted_seconds)
  values (new.id, 18000, 18000);

  insert into public.credit_transactions (user_id, amount_seconds, type, description)
  values (new.id, 18000, 'signup_bonus', '가입 보너스 5시간');

  return new;
end;
$$ language plpgsql security definer;

-- ============================================================
-- CREDIT TRANSACTIONS
-- ============================================================
create table public.credit_transactions (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  amount_seconds integer not null,
  type text not null check (type in ('signup_bonus', 'purchase', 'hold', 'hold_release', 'usage', 'refund')),
  job_id uuid,
  description text,
  created_at timestamptz not null default now()
);

create index idx_credit_transactions_user on public.credit_transactions(user_id);
create index idx_credit_transactions_job on public.credit_transactions(job_id);

alter table public.credit_transactions enable row level security;

create policy "Users can read own transactions"
  on public.credit_transactions for select
  using (auth.uid() = user_id);

-- Now create the trigger (after credit_transactions table exists)
create trigger on_profile_created
  after insert on public.profiles
  for each row execute function public.handle_new_profile();

-- ============================================================
-- PROJECTS
-- ============================================================
create table public.projects (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  name text not null,
  status text not null default 'created' check (status in ('created', 'uploading', 'queued', 'processing', 'completed', 'failed')),
  cut_type text not null check (cut_type in ('subtitle_cut', 'podcast_cut')),
  language text not null default 'ko',
  source_r2_key text,
  source_filename text,
  source_duration_seconds integer,
  source_size_bytes bigint,
  settings jsonb not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_projects_user on public.projects(user_id);
create index idx_projects_status on public.projects(status);

alter table public.projects enable row level security;

create policy "Users can read own projects"
  on public.projects for select
  using (auth.uid() = user_id);

create policy "Users can insert own projects"
  on public.projects for insert
  with check (auth.uid() = user_id);

create policy "Users can update own projects"
  on public.projects for update
  using (auth.uid() = user_id);

create policy "Users can delete own projects"
  on public.projects for delete
  using (auth.uid() = user_id);

-- Auto-update updated_at
create or replace function public.update_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger projects_updated_at
  before update on public.projects
  for each row execute function public.update_updated_at();

-- ============================================================
-- JOBS
-- ============================================================
create table public.jobs (
  id uuid primary key default uuid_generate_v4(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  type text not null check (type in ('transcribe', 'transcript_overview', 'subtitle_cut', 'podcast_cut')),
  status text not null default 'queued' check (status in ('queued', 'running', 'completed', 'failed')),
  progress integer not null default 0 check (progress >= 0 and progress <= 100),
  result_r2_keys jsonb,
  error_message text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now()
);

create index idx_jobs_project on public.jobs(project_id);
create index idx_jobs_user on public.jobs(user_id);
create index idx_jobs_status on public.jobs(status);

alter table public.jobs enable row level security;

create policy "Users can read own jobs"
  on public.jobs for select
  using (auth.uid() = user_id);

-- ============================================================
-- EDIT REPORTS
-- ============================================================
create table public.edit_reports (
  id uuid primary key default uuid_generate_v4(),
  project_id uuid not null references public.projects(id) on delete cascade unique,
  total_duration_seconds integer not null,
  cut_duration_seconds integer not null,
  cut_percentage real not null,
  edit_summary jsonb not null default '{}',
  report_markdown text not null,
  created_at timestamptz not null default now()
);

create index idx_edit_reports_project on public.edit_reports(project_id);

alter table public.edit_reports enable row level security;

create policy "Users can read own reports"
  on public.edit_reports for select
  using (
    exists (
      select 1 from public.projects
      where projects.id = edit_reports.project_id
      and projects.user_id = auth.uid()
    )
  );
