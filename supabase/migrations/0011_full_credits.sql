-- Full per-item credits for a render, shown on a public credits page linked from the YouTube description
-- (the description itself carries a short grouped summary to stay under YouTube's 5,000-character limit).
alter table renders add column if not exists credits_full text;
create or replace function public_credits(p_render uuid)
returns table (title text, credits text) language sql stable security definer set search_path = public as $$
  select coalesce(p.selected_title, p.title), r.credits_full from renders r join projects p on p.id = r.project_id
  where r.id = p_render and r.credits_full is not null
$$;
do $$ begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'grant execute on function public_credits(uuid) to anon, authenticated';
  end if;
end $$;
