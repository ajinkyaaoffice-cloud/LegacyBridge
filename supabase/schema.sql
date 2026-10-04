-- LegacyBridge: Supabase schema for the hosted-auth + ledger mirror (optional).
-- Run once in the Supabase SQL editor. The server writes with the service role key;
-- signed-in users can read their own rows straight from the browser thanks to RLS.

create table if not exists public.lb_profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  handle text not null,
  provider text not null default 'email',
  updated_at timestamptz not null default now()
);

create table if not exists public.lb_runs (
  id text primary key,
  owner uuid not null references auth.users(id) on delete cascade,
  title text not null,
  source_kind text not null,
  dry_run boolean not null,
  fuzz boolean not null,
  status text not null,
  exit_code integer,
  created_at timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  summary jsonb
);
create index if not exists lb_runs_owner on public.lb_runs(owner, created_at desc);

alter table public.lb_profiles enable row level security;
alter table public.lb_runs enable row level security;

drop policy if exists "own profile" on public.lb_profiles;
create policy "own profile" on public.lb_profiles for select using (auth.uid() = id);
drop policy if exists "own runs" on public.lb_runs;
create policy "own runs" on public.lb_runs for select using (auth.uid() = owner);
