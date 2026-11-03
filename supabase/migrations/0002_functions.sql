-- Business rules that must hold no matter which client writes: spend cap, queue claim,
-- publish checklist, and the publish gate.

-- ---------------------------------------------------------------- spend
create or replace function month_spend_usd(p_user uuid)
returns numeric language sql stable as $$
  select coalesce(sum(cost_usd),0)::numeric
  from cost_ledger
  where user_id = p_user
    and created_at >= date_trunc('month', now());
$$;

-- True if spending p_amount more would stay within the user's hard monthly cap.
create or replace function within_cap(p_user uuid, p_amount numeric)
returns boolean language sql stable as $$
  select month_spend_usd(p_user) + coalesce(p_amount,0) <= (select monthly_cap_usd from users where id = p_user);
$$;

-- "Videos like this cost $X last time": average actual spend for finished renders at a tier/length.
create or replace function past_render_cost(p_user uuid, p_tier text, p_minutes int)
returns table(renders int, avg_cost_usd numeric) language sql stable as $$
  with r as (
    select r.id, sum(l.cost_usd) as cost
    from renders r join projects p on p.id = r.project_id
    join cost_ledger l on l.render_id = r.id
    where p.user_id = p_user and p.tier = p_tier and p.target_minutes = p_minutes and r.status = 'succeeded'
    group by r.id)
  select count(*)::int, round(avg(cost)::numeric, 4) from r;
$$;

create or replace view spend_by_provider_month as
  select user_id, date_trunc('month', created_at) as month, provider,
         sum(cost_usd)::numeric(12,4) as cost_usd, count(*) as calls
  from cost_ledger group by 1,2,3;

-- ---------------------------------------------------------------- queue
-- Claim one runnable job. Expired leases (crashed worker) are reclaimable.
create or replace function claim_job(p_worker text, p_lease_seconds int default 120, p_kinds text[] default null)
returns setof jobs language plpgsql as $$
declare j jobs;
begin
  select * into j from jobs
   where ((status = 'queued' and run_after <= now())
       or (status = 'running' and locked_until < now()))
     and (p_kinds is null or kind = any(p_kinds))
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

create or replace function heartbeat_job(p_job uuid, p_worker text, p_lease_seconds int, p_progress numeric, p_note text)
returns boolean language sql as $$
  update jobs set locked_until = now() + make_interval(secs => p_lease_seconds),
                  progress = coalesce(p_progress, progress), progress_note = coalesce(p_note, progress_note)
   where id = p_job and locked_by = p_worker and status = 'running'
  returning true;
$$;

create or replace function fail_job(p_job uuid, p_worker text, p_error text)
returns jobs language plpgsql as $$
declare j jobs;
begin
  update jobs set
    status = case when attempts >= max_attempts then 'failed'::job_status else 'queued'::job_status end,
    run_after = now() + make_interval(secs => least(600, (power(2, attempts) * 15)::int)),  -- 30s, 60s, 120s...
    locked_by = null, locked_until = null, last_error = left(p_error, 4000),
    finished_at = case when attempts >= max_attempts then now() else null end
  where id = p_job and locked_by = p_worker returning * into j;
  return j;
end $$;

-- ---------------------------------------------------------------- publish checklist
-- Single source of truth for the pre-publish checklist (UI shows it, the DB enforces it).
create or replace function publish_checklist(p_project uuid, p_render uuid)
returns jsonb language plpgsql stable as $$
declare
  p projects;
  items jsonb := '[]'::jsonb;
  n_scenes int; n_scenes_with_footage int; n_unchecked int; n_dnu int; n_unsourced_real int;
  has_signoff boolean; n_disputed_unflagged int; thumb_graphic boolean; thumb_exists boolean;
  title text; descr text; disclosure text;
begin
  select * into p from projects where id = p_project;
  if not found then raise exception 'project % not found', p_project; end if;

  select count(*) into n_scenes from scenes where project_id = p_project;
  -- footage logged: every scene has a selected visual (an explicit gap card counts, it is logged)
  select count(distinct s.id) into n_scenes_with_footage
    from scenes s join assets a on a.scene_id = s.id and a.selected
   where s.project_id = p_project;
  select count(*) into n_unchecked from assets
   where project_id = p_project and selected and tier in ('real','news_document') and not rights_checked;
  select count(*) into n_dnu from assets where project_id = p_project and selected and license = 'Do Not Use';
  select count(*) into n_unsourced_real from assets
   where project_id = p_project and selected and tier in ('real','news_document')
     and (source_url is null or agency is null);
  select exists(select 1 from blur_signoffs where render_id = p_render and scrubbed_full_video and frames_checked > 0)
    into has_signoff;
  select exists(select 1 from assets where project_id = p_project and kind = 'thumbnail' and selected) into thumb_exists;
  select coalesce(bool_or(graphic or coalesce((meta->>'from_graphic_frame')::boolean,false)), false) into thumb_graphic
    from assets where project_id = p_project and kind = 'thumbnail' and selected;

  title := coalesce(p.selected_title, '');
  descr := coalesce(p.description, '');
  disclosure := coalesce(p.disclosure_line, '');

  items := items || jsonb_build_object('key','footage_logged','label','Footage logged for every scene',
     'pass', n_scenes > 0 and n_scenes_with_footage = n_scenes and n_unsourced_real = 0,
     'detail', format('%s/%s scenes have a logged visual; %s real/news items missing source', n_scenes_with_footage, n_scenes, n_unsourced_real));
  items := items || jsonb_build_object('key','rights_checked','label','Rights checked, no "Do Not Use" clips',
     'pass', n_unchecked = 0 and n_dnu = 0,
     'detail', format('%s unchecked, %s Do Not Use on timeline', n_unchecked, n_dnu));
  items := items || jsonb_build_object('key','blur_signed_off','label','Blur pass frame-checked and signed off',
     'pass', has_signoff, 'detail', case when has_signoff then 'signed' else 'scrub the video and sign the blur pass' end);
  items := items || jsonb_build_object('key','fact_checked','label','Script fact-checked',
     'pass', p.fact_check_signed_at is not null,
     'detail', case when p.fact_check_signed_at is null then 'review claims and sign the fact-check' else 'signed' end);
  items := items || jsonb_build_object('key','title_length','label','Title under 60 characters',
     'pass', length(title) between 1 and 59, 'detail', format('%s characters', length(title)));
  items := items || jsonb_build_object('key','chapters_in_description','label','Description has chapters (starts at 0:00)',
     'pass', descr ~ '(^|\n)0:00 ' and (select count(*) from regexp_matches(descr, '(^|\n)\d{1,2}:\d{2}(:\d{2})? ', 'g')) >= 3,
     'detail', 'YouTube needs 0:00 first and at least 3 timestamps');
  items := items || jsonb_build_object('key','disclosure','label','Disclosure line present',
     'pass', length(disclosure) > 0 and position(disclosure in descr) > 0,
     'detail', 'disclosure must appear in the description');
  items := items || jsonb_build_object('key','thumbnail_safe','label','Thumbnail exists and is not a graphic frame',
     'pass', thumb_exists and not thumb_graphic, 'detail', case when not thumb_exists then 'no thumbnail selected' else '' end);
  items := items || jsonb_build_object('key','approved','label','Final video approved by you',
     'pass', p.approved_at is not null, 'detail', '');

  return jsonb_build_object('pass', not exists (select 1 from jsonb_array_elements(items) e where not (e->>'pass')::boolean),
                            'items', items);
end $$;

-- The gate: no publish row can be created (or moved to uploading) unless the checklist passes.
create or replace function enforce_publish_gate() returns trigger language plpgsql as $$
declare c jsonb;
begin
  if new.status = 'manual' then return new; end if; -- manual download path records intent only
  c := publish_checklist(new.project_id, new.render_id);
  if not (c->>'pass')::boolean then
    raise exception 'publish blocked: checklist incomplete %', c->'items' using errcode = 'P0001';
  end if;
  if not exists (select 1 from renders where id = new.render_id and status = 'succeeded' and has_audio) then
    raise exception 'publish blocked: render is not finished or has no audio stream' using errcode = 'P0001';
  end if;
  return new;
end $$;

drop trigger if exists publishes_gate on publishes;
create trigger publishes_gate before insert on publishes
  for each row execute function enforce_publish_gate();

-- A render without an audio stream can never be marked succeeded.
create or replace function enforce_render_audio() returns trigger language plpgsql as $$
begin
  if new.status = 'succeeded' and coalesce(new.has_audio,false) = false then
    raise exception 'render % cannot succeed without an audio stream (silent export = failed build)', new.id;
  end if;
  return new;
end $$;
drop trigger if exists renders_audio on renders;
create trigger renders_audio before insert or update on renders
  for each row execute function enforce_render_audio();

-- Approving a project requires the blur sign-off on its latest successful render.
create or replace function enforce_approval() returns trigger language plpgsql as $$
begin
  if new.approved_at is not null and old.approved_at is null then
    if not exists (
      select 1 from blur_signoffs b join renders r on r.id = b.render_id
       where b.project_id = new.id and r.status = 'succeeded'
         and r.version = (select max(version) from renders where project_id = new.id and status = 'succeeded')) then
      raise exception 'approve blocked: sign off the blur pass on the latest render first';
    end if;
  end if;
  new.updated_at := now();
  return new;
end $$;
drop trigger if exists projects_approval on projects;
create trigger projects_approval before update on projects
  for each row execute function enforce_approval();
