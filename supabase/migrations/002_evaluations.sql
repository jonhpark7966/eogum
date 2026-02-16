-- 구간 리뷰 평가 테이블
-- Stores human evaluation of AI edit decisions per segment

create table public.evaluations (
  id uuid primary key default uuid_generate_v4(),
  project_id uuid not null references public.projects(id) on delete cascade,
  evaluator_id uuid not null references public.profiles(id) on delete cascade,
  version text not null default '1.0',
  avid_version text,
  eogum_version text,
  segments jsonb not null default '[]',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index idx_evaluations_project_evaluator
  on public.evaluations(project_id, evaluator_id);

create index idx_evaluations_project on public.evaluations(project_id);

alter table public.evaluations enable row level security;

create policy "Users can read own evaluations"
  on public.evaluations for select
  using (auth.uid() = evaluator_id);

create policy "Users can insert own evaluations"
  on public.evaluations for insert
  with check (auth.uid() = evaluator_id);

create policy "Users can update own evaluations"
  on public.evaluations for update
  using (auth.uid() = evaluator_id);

-- Reuse existing update_updated_at trigger function
create trigger evaluations_updated_at
  before update on public.evaluations
  for each row execute function public.update_updated_at();
