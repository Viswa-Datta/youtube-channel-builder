-- Real-footage workflow: case-based ideas, help requests (things only the user can do), a Footage Desk pause,
-- and 45/60-minute videos.

-- ---------------------------------------------------------------- longer videos
alter table channels drop constraint if exists channels_target_minutes_check;
alter table channels add constraint channels_target_minutes_check check (target_minutes in (8,12,20,30,45,60));
alter table projects drop constraint if exists projects_target_minutes_check;
alter table projects add constraint projects_target_minutes_check check (target_minutes in (8,12,20,30,45,60));

-- ---------------------------------------------------------------- case-based ideas
alter table ideas add column if not exists case_info jsonb not null default '{}'::jsonb;       -- case_name, location, year, agency, case_status
alter table ideas add column if not exists footage_evidence jsonb not null default '[]'::jsonb; -- what the search actually found (title, url, source, kind)
alter table ideas add column if not exists case_verified boolean not null default false;      -- a reference source (e.g. Wikipedia) confirms the case exists

-- ---------------------------------------------------------------- renders pause at the Footage Desk
alter table renders drop constraint if exists renders_status_check;
alter table renders add constraint renders_status_check
  check (status in ('queued','running','waiting_user','succeeded','failed','paused_cap','cancelled'));

-- ---------------------------------------------------------------- help requests
-- Created by the worker when something needs a person: saving a clip the app may not download itself
-- (e.g. an agency's YouTube upload), confirming rights, or finding a source. Never auto-completed.
create table if not exists help_requests (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  scene_id uuid references scenes(id) on delete set null,
  kind text not null check (kind in ('save_clip','confirm_rights','find_source')),
  title text not null,
  instructions text not null,
  source_url text,
  source_name text,
  agency text,
  release_date date,
  start_sec numeric(10,3),
  end_sec numeric(10,3),
  license_hint license_status,
  rights_note text,
  status text not null default 'open' check (status in ('open','done','skipped')),
  asset_id uuid references assets(id) on delete set null,
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  constraint done_save_clip_has_asset check (not (kind = 'save_clip' and status = 'done' and asset_id is null))
);
create index if not exists help_requests_project_idx on help_requests(project_id, status);

alter table help_requests enable row level security;
drop policy if exists own_rows on help_requests;
create policy own_rows on help_requests for all using (owns_project(project_id)) with check (owns_project(project_id));
