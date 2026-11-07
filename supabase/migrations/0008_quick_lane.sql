-- Three worker lanes: 'voice' (narration), 'main' (other render stages: footage, blur, assemble, package),
-- 'quick' (everything that is not a render stage: ideas, channel kits, production packs, publishing).
create or replace function claim_job_lane(p_worker text, p_lease_seconds int, p_lane text)
returns setof jobs language plpgsql as $$
declare j jobs;
begin
  select * into j from jobs
   where ((status = 'queued' and run_after <= now())
       or (status = 'running' and locked_until < now()))
     and case when p_lane = 'voice' then kind = 'render_stage' and stage = 'voice'
              when p_lane = 'quick' then kind <> 'render_stage'
              else kind = 'render_stage' and coalesce(stage, '') <> 'voice' end
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
