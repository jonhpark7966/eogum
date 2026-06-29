-- Atomic credit operations for concurrent project workers.

create or replace function public.hold_credits_atomic(
  p_user_id uuid,
  p_seconds integer,
  p_job_id uuid default null
)
returns table (
  user_id uuid,
  balance_seconds integer,
  held_seconds integer,
  total_granted_seconds integer,
  updated_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_seconds <= 0 then
    raise exception 'p_seconds must be positive';
  end if;

  return query
  with updated as (
    update public.credits c
    set
      held_seconds = c.held_seconds + p_seconds,
      updated_at = now()
    where c.user_id = p_user_id
      and c.balance_seconds - c.held_seconds >= p_seconds
    returning
      c.user_id,
      c.balance_seconds,
      c.held_seconds,
      c.total_granted_seconds,
      c.updated_at
  ),
  inserted as (
    insert into public.credit_transactions (
      user_id,
      amount_seconds,
      type,
      job_id,
      description
    )
    select
      updated.user_id,
      -p_seconds,
      'hold',
      p_job_id,
      format('처리 시작 홀딩 (%s초)', p_seconds)
    from updated
    returning 1
  )
  select
    updated.user_id,
    updated.balance_seconds,
    updated.held_seconds,
    updated.total_granted_seconds,
    updated.updated_at
  from updated;
end;
$$;

create or replace function public.confirm_usage_atomic(
  p_user_id uuid,
  p_seconds integer,
  p_job_id uuid default null
)
returns table (
  user_id uuid,
  balance_seconds integer,
  held_seconds integer,
  total_granted_seconds integer,
  updated_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_seconds <= 0 then
    raise exception 'p_seconds must be positive';
  end if;

  return query
  with updated as (
    update public.credits c
    set
      balance_seconds = c.balance_seconds - p_seconds,
      held_seconds = c.held_seconds - p_seconds,
      updated_at = now()
    where c.user_id = p_user_id
      and c.held_seconds >= p_seconds
    returning
      c.user_id,
      c.balance_seconds,
      c.held_seconds,
      c.total_granted_seconds,
      c.updated_at
  ),
  inserted as (
    insert into public.credit_transactions (
      user_id,
      amount_seconds,
      type,
      job_id,
      description
    )
    select
      updated.user_id,
      -p_seconds,
      'usage',
      p_job_id,
      format('처리 완료 (%s초 사용)', p_seconds)
    from updated
    returning 1
  )
  select
    updated.user_id,
    updated.balance_seconds,
    updated.held_seconds,
    updated.total_granted_seconds,
    updated.updated_at
  from updated;
end;
$$;

create or replace function public.release_hold_atomic(
  p_user_id uuid,
  p_seconds integer,
  p_job_id uuid default null
)
returns table (
  user_id uuid,
  balance_seconds integer,
  held_seconds integer,
  total_granted_seconds integer,
  updated_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_seconds <= 0 then
    raise exception 'p_seconds must be positive';
  end if;

  return query
  with locked as (
    select c.user_id, c.held_seconds
    from public.credits c
    where c.user_id = p_user_id
    for update
  ),
  updated as (
    update public.credits c
    set
      held_seconds = greatest(0, c.held_seconds - p_seconds),
      updated_at = now()
    from locked
    where c.user_id = locked.user_id
      and locked.held_seconds > 0
    returning
      c.user_id,
      c.balance_seconds,
      c.held_seconds,
      c.total_granted_seconds,
      c.updated_at,
      least(p_seconds, locked.held_seconds) as released_seconds
  ),
  inserted as (
    insert into public.credit_transactions (
      user_id,
      amount_seconds,
      type,
      job_id,
      description
    )
    select
      updated.user_id,
      updated.released_seconds,
      'hold_release',
      p_job_id,
      format('처리 실패 홀딩 해제 (%s초 복구)', updated.released_seconds)
    from updated
    returning 1
  )
  select
    updated.user_id,
    updated.balance_seconds,
    updated.held_seconds,
    updated.total_granted_seconds,
    updated.updated_at
  from updated;
end;
$$;

revoke all on function public.hold_credits_atomic(uuid, integer, uuid) from public, anon, authenticated;
revoke all on function public.confirm_usage_atomic(uuid, integer, uuid) from public, anon, authenticated;
revoke all on function public.release_hold_atomic(uuid, integer, uuid) from public, anon, authenticated;

grant execute on function public.hold_credits_atomic(uuid, integer, uuid) to service_role;
grant execute on function public.confirm_usage_atomic(uuid, integer, uuid) to service_role;
grant execute on function public.release_hold_atomic(uuid, integer, uuid) to service_role;
