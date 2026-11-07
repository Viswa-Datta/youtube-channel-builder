-- YouTube Shorts: each documentary gets its own vertical Shorts (new script, voice and visuals made from the same
-- research). They are posted on the days between long videos and point viewers to the full documentary.
create table if not exists shorts (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  idx int not null,                         -- 0, 1, ... within the project
  status text not null default 'queued' check (status in ('queued','running','succeeded','failed')),
  title text,                               -- YouTube title (with #Shorts)
  hook text,                                -- on-screen hook shown in the first seconds
  narration text,
  description text,
  hashtags text[] not null default '{}',
  lines jsonb,                              -- [{text, asset_id, ai, start, end}]
  mp4_path text, srt_path text, thumb_path text,
  duration_sec numeric(8,3),
  error text,
  planned_at timestamptz,                   -- slot in YouTube Studio
  posted_at timestamptz,
  youtube_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (project_id, idx)
);
create index if not exists shorts_project on shorts(project_id);

alter table shorts enable row level security;
drop policy if exists own_rows on shorts;
create policy own_rows on shorts for all using (owns_project(project_id)) with check (owns_project(project_id));
do $$ begin
  execute 'revoke all on table shorts from anon';
exception when others then null;
end $$;

-- Shorts are rendered on the 'main' lane (heavy FFmpeg work), never on 'quick', so they do not hold up ideas.
create or replace function claim_job_lane(p_worker text, p_lease_seconds int, p_lane text)
returns setof jobs language plpgsql as $$
declare j jobs;
begin
  select * into j from jobs
   where ((status = 'queued' and run_after <= now())
       or (status = 'running' and locked_until < now()))
     and case when p_lane = 'voice' then kind = 'render_stage' and stage = 'voice'
              when p_lane = 'quick' then kind not in ('render_stage', 'shorts')
              else (kind = 'render_stage' and coalesce(stage, '') <> 'voice') or kind = 'shorts' end
   order by priority, run_after, created_at
   for update skip locked
   limit 1;
  if not found then return; end if;
  update jobs set status = 'running', locked_by = p_worker,
         locked_until = now() + make_interval(secs => p_lease_seconds),
         attempts = attempts + 1, started_at = coalesce(started_at, now()), last_error =
           case when j.status = 'running' then coalesce(j.last_error,'') || ' | lease expired (worker lost), resumed' else j.last_error end
   where id = j.id returning * into j;
  return next j;
end $$;
