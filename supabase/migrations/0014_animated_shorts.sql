-- Animated Shorts channels: every video is a 45-60 s vertical Short made of AI 3D scenes with one recurring
-- original character (reference image kept on the channel so every scene shows the same person).
alter table channels add column if not exists format text not null default 'documentary';
do $$ begin
  alter table channels add constraint channels_format_check check (format in ('documentary', 'animated_shorts'));
exception when duplicate_object then null;
end $$;
alter table channels add column if not exists character jsonb;  -- {name, look, ref_path}

-- Shorts can be the project's own video (animated channel) and carry a designed cover image.
alter table shorts add column if not exists kind text not null default 'from_video';
do $$ begin
  alter table shorts add constraint shorts_kind_check check (kind in ('from_video', 'animated'));
exception when duplicate_object then null;
end $$;
alter table shorts add column if not exists cover_path text;

-- Animated Shorts and Shorts both run on the main lane (heavy FFmpeg / generation work).
create or replace function claim_job_lane(p_worker text, p_lease_seconds int, p_lane text)
returns setof jobs language plpgsql as $$
declare j jobs;
begin
  select * into j from jobs
   where ((status = 'queued' and run_after <= now())
       or (status = 'running' and locked_until < now()))
     and case when p_lane = 'voice' then kind = 'render_stage' and stage = 'voice'
              when p_lane = 'quick' then kind not in ('render_stage', 'shorts', 'animated_short')
              else (kind = 'render_stage' and coalesce(stage, '') <> 'voice') or kind in ('shorts', 'animated_short') end
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
