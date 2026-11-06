-- Two worker lanes: 'voice' (narration recording, hours on CPU) and 'main' (everything else),
-- so short steps of other videos never wait behind a long voice recording.
create or replace function claim_job_lane(p_worker text, p_lease_seconds int, p_lane text)
returns setof jobs language plpgsql as $$
declare j jobs;
begin
  select * into j from jobs
   where ((status = 'queued' and run_after <= now())
       or (status = 'running' and locked_until < now()))
     and case when p_lane = 'voice' then kind = 'render_stage' and stage = 'voice'
              else not (kind = 'render_stage' and coalesce(stage, '') = 'voice') end
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
revoke execute on function claim_job_lane(text, int, text) from public;
do $$ begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke execute on function claim_job_lane(text, int, text) from anon, authenticated';
  end if;
end $$;
