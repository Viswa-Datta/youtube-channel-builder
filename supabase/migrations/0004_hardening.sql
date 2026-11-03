-- Security hardening (Supabase database linter): pin search_path, restrict helper functions.
alter function public.enforce_publish_gate() set search_path = public;
alter function public.enforce_render_audio() set search_path = public;
alter function public.enforce_approval() set search_path = public;
alter function public.claim_job(text, int, text[]) set search_path = public;
alter function public.heartbeat_job(uuid, text, int, numeric, text) set search_path = public;
alter function public.fail_job(uuid, text, text) set search_path = public;
alter function public.month_spend_usd(uuid) set search_path = public;
alter function public.within_cap(uuid, numeric) set search_path = public;
alter function public.past_render_cost(uuid, text, int) set search_path = public;
alter function public.publish_checklist(uuid, uuid) set search_path = public;
revoke execute on function public.handle_new_user() from public;
revoke execute on function public.owns_project(uuid) from public;
revoke execute on function public.owns_channel(uuid) from public;
revoke execute on function public.within_cap(uuid, numeric) from public;
revoke execute on function public.month_spend_usd(uuid) from public;
revoke execute on function public.past_render_cost(uuid, text, int) from public;
revoke execute on function public.publish_checklist(uuid, uuid) from public;
do $$ begin
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    execute 'revoke execute on function public.handle_new_user() from anon, authenticated';
    execute 'revoke execute on function public.owns_project(uuid), public.owns_channel(uuid) from anon';
    execute 'grant execute on function public.owns_project(uuid), public.owns_channel(uuid) to authenticated';
    execute 'revoke execute on function public.within_cap(uuid, numeric), public.month_spend_usd(uuid), public.past_render_cost(uuid, text, int), public.publish_checklist(uuid, uuid) from anon';
    execute 'grant execute on function public.within_cap(uuid, numeric), public.month_spend_usd(uuid), public.past_render_cost(uuid, text, int), public.publish_checklist(uuid, uuid) to authenticated';
  end if;
end $$;
