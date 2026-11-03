-- Row level security: the browser/Next.js user session only ever sees its own rows.
-- The worker connects with the service connection string and bypasses RLS.

alter table users enable row level security;
alter table channels enable row level security;
alter table channel_kits enable row level security;
alter table ideas enable row level security;
alter table projects enable row level security;
alter table scenes enable row level security;
alter table research_sources enable row level security;
alter table research_claims enable row level security;
alter table assets enable row level security;
alter table footage_plan enable row level security;
alter table foia_requests enable row level security;
alter table redaction_plan enable row level security;
alter table renders enable row level security;
alter table blur_signoffs enable row level security;
alter table youtube_connections enable row level security;
alter table publishes enable row level security;
alter table youtube_quota enable row level security;
alter table cost_ledger enable row level security;
alter table estimates enable row level security;
alter table jobs enable row level security;

create or replace function owns_project(p uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from projects where id = p and user_id = auth.uid());
$$;
create or replace function owns_channel(c uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from channels where id = c and user_id = auth.uid());
$$;

drop policy if exists own_user on users;
create policy own_user on users for all using (id = auth.uid()) with check (id = auth.uid());

drop policy if exists own_channels on channels;
create policy own_channels on channels for all using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists own_kits on channel_kits;
create policy own_kits on channel_kits for all using (owns_channel(channel_id)) with check (owns_channel(channel_id));

drop policy if exists own_ideas on ideas;
create policy own_ideas on ideas for all using (owns_channel(channel_id)) with check (owns_channel(channel_id));

drop policy if exists own_projects on projects;
create policy own_projects on projects for all using (user_id = auth.uid()) with check (user_id = auth.uid());

do $$
declare t text;
begin
  foreach t in array array['scenes','research_sources','research_claims','assets','footage_plan',
                           'foia_requests','redaction_plan','renders','blur_signoffs','publishes'] loop
    execute format('drop policy if exists own_rows on %I', t);
    execute format('create policy own_rows on %I for all using (owns_project(project_id)) with check (owns_project(project_id))', t);
  end loop;
end $$;

-- Ledger, estimates, jobs, quota: users can read their own; only the server writes ledger/jobs.
drop policy if exists read_own on cost_ledger;
create policy read_own on cost_ledger for select using (user_id = auth.uid());
drop policy if exists own_est on estimates;
create policy own_est on estimates for all using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists read_own_jobs on jobs;
create policy read_own_jobs on jobs for select using (user_id = auth.uid());
drop policy if exists read_own_quota on youtube_quota;
create policy read_own_quota on youtube_quota for select using (user_id = auth.uid());

-- OAuth tokens: never readable from the browser. Users may read only the status columns of their own row
-- (column-level grant), through a security-invoker view.
drop policy if exists own_connection_status on youtube_connections;
create policy own_connection_status on youtube_connections for select using (user_id = auth.uid());
do $$ begin
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    execute 'revoke all on table youtube_connections from anon, authenticated';
    execute 'grant select (user_id, channel_id, channel_title, scopes, audited_project, connected_at, expires_at) on youtube_connections to authenticated';
  end if;
end $$;
create or replace view youtube_connection_status with (security_invoker = true) as
  select user_id, channel_id, channel_title, scopes, audited_project, connected_at, expires_at
    from youtube_connections where user_id = auth.uid();

-- New Supabase Auth user -> profile row.
create or replace function handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.users (id, email, display_name)
  values (new.id, new.email, coalesce(new.raw_user_meta_data->>'full_name', split_part(new.email,'@',1)))
  on conflict (id) do nothing;
  return new;
end $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function handle_new_user();

-- Storage bucket for project media (private; files are served by signed URL).
do $$ begin
  if exists (select 1 from pg_namespace where nspname = 'storage') then
    insert into storage.buckets (id, name, public) values ('projects','projects', false) on conflict do nothing;
  end if;
end $$;

-- Queue/ledger internals are for the worker only: not callable through the Supabase API.
revoke execute on function claim_job(text, int, text[]) from public;
revoke execute on function heartbeat_job(uuid, text, int, numeric, text) from public;
revoke execute on function fail_job(uuid, text, text) from public;
do $$ begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke execute on function claim_job(text, int, text[]) from anon, authenticated';
    execute 'revoke execute on function heartbeat_job(uuid, text, int, numeric, text) from anon, authenticated';
    execute 'revoke execute on function fail_job(uuid, text, text) from anon, authenticated';
    execute 'revoke all on table jobs from anon';
    execute 'revoke insert, update, delete on table jobs, cost_ledger, youtube_connections from authenticated';
  end if;
end $$;
