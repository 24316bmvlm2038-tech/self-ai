-- Self-AI web version: storage and scheduler in a (free) Supabase project.
--
-- Run this once in the Supabase SQL editor, then save your settings (bottom of this file).
-- The tables are only reachable with the app's secret (X-SelfAI-Secret header), enforced by
-- row-level security; the public API key alone can't read or change anything.

create extension if not exists pg_net with schema extensions;
create extension if not exists pg_cron;

-- ------------------------------------------------------------------ app data
-- Times are Unix timestamps in seconds, like the SQLite version (selfai/db.py).

create table public.conversations (
  id text primary key,
  title text not null,
  created_at double precision not null,
  updated_at double precision not null
);

create table public.messages (
  id bigint generated always as identity primary key,
  conversation_id text not null references public.conversations (id) on delete cascade,
  role text not null,
  content text,
  tool_calls text,
  tool_call_id text,
  name text,
  reasoning text,
  created_at double precision not null
);
create index messages_conversation on public.messages (conversation_id, id);

create table public.tasks (
  id text primary key,
  title text not null,
  goal text not null,
  status text not null,
  interval_minutes double precision,
  next_run_at double precision,
  created_by text not null default 'user',
  last_result text,
  last_run_status text,
  run_count integer not null default 0,
  created_at double precision not null,
  updated_at double precision not null
);
create index tasks_due on public.tasks (status, next_run_at);

create table public.task_runs (
  id bigint generated always as identity primary key,
  task_id text not null references public.tasks (id) on delete cascade,
  status text not null,
  result text,
  started_at double precision not null,
  finished_at double precision
);
create index task_runs_task on public.task_runs (task_id, id);
create index task_runs_status on public.task_runs (status, started_at);

create table public.task_events (
  id bigint generated always as identity primary key,
  run_id bigint not null references public.task_runs (id) on delete cascade,
  type text not null,
  data text not null,
  created_at double precision not null
);
create index task_events_run on public.task_events (run_id, id);

create table public.settings (
  key text primary key,
  value text not null
);

-- Files the AI creates (the web server's own disk is temporary). Contents are base64.
create table public.files (
  path text primary key,
  data text not null,
  sha text not null,
  size bigint not null,
  updated_at double precision not null
);

-- ------------------------------------------------------------------ access control

-- Not exposed through the API.
create schema if not exists private;
create table private.config (
  key text primary key,  -- 'db_secret', 'cron_secret', 'tick_url'
  value text not null
);

create or replace function private.authorized() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from private.config c
    where c.key = 'db_secret'
      and c.value = coalesce(current_setting('request.headers', true)::json ->> 'x-selfai-secret', '')
  )
$$;
revoke all on function private.authorized() from public;
grant usage on schema private to anon;
grant execute on function private.authorized() to anon;

do $$
declare t text;
begin
  foreach t in array array['conversations', 'messages', 'tasks', 'task_runs', 'task_events', 'settings', 'files'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format(
      'create policy "Self-AI server only" on public.%I for all to anon '
      'using ((select private.authorized())) with check ((select private.authorized()))', t);
  end loop;
end $$;

-- ------------------------------------------------------------------ background tasks
-- The web server only runs while answering a request, so the database wakes it up:
-- right away when a task becomes due now, and every minute for scheduled ones.

create or replace function private.wake_server() returns void
language plpgsql security definer set search_path = '' as $$
declare
  url text := (select value from private.config where key = 'tick_url');
  secret text := (select value from private.config where key = 'cron_secret');
begin
  if url is null or secret is null then
    return;
  end if;
  perform net.http_post(
    url := url,
    body := '{}'::jsonb,
    headers := jsonb_build_object('Content-Type', 'application/json', 'X-SelfAI', '1', 'X-SelfAI-Cron', secret),
    timeout_milliseconds := 300000  -- a run may take up to 5 minutes
  );
end $$;
revoke all on function private.wake_server() from public;

create or replace function private.wake_for_due_task() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if new.status = 'pending' and new.next_run_at <= extract(epoch from now())::double precision + 5 then
    perform private.wake_server();
  end if;
  return new;
end $$;
revoke all on function private.wake_for_due_task() from public;

create trigger wake_for_due_task
after insert or update of status, next_run_at on public.tasks
for each row execute function private.wake_for_due_task();

-- Every minute, if a task is due or a run was cut off; plus once a day so the free project
-- stays active.
select cron.schedule('selfai-tick', '* * * * *', $$
  select private.wake_server()
  where exists (
      select 1 from public.tasks
      where status = 'pending' and next_run_at <= extract(epoch from now())::double precision)
    or exists (
      select 1 from public.task_runs
      where status = 'running' and started_at < extract(epoch from now())::double precision - 420)
    or (extract(hour from now()) = 4 and extract(minute from now()) = 0)
$$);

-- ------------------------------------------------------------------ your settings
-- Use long random values for both secrets, and put the same values in the web server's
-- environment variables (SELFAI_DB_SECRET and SELFAI_CRON_SECRET):
--
-- insert into private.config (key, value) values
--   ('db_secret', '<random secret 1>'),
--   ('cron_secret', '<random secret 2>'),
--   ('tick_url', 'https://<your-app>.vercel.app/api/cron/tick');
