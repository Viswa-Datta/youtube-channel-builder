-- YouTube Channel Builder: core schema
-- Runs on Supabase Postgres (auth.users provided by Supabase Auth).
-- For local tests without Supabase, apply supabase/local/auth_stub.sql first.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- enums
do $$ begin
  create type idea_status as enum ('suggested','shortlisted','in_production','ready','published','dismissed');
exception when duplicate_object then null; end $$;

do $$ begin
  create type license_status as enum (
    'Public Domain','Public Record with release terms','Licensed','Commentary-only','Do Not Use');
exception when duplicate_object then null; end $$;

do $$ begin
  -- Visual order per scene: real case footage > news/documents on camera > stock B-roll.
  -- 'generated' = cards/graphics made by the app; 'gap' = no verified clip found.
  create type footage_tier as enum ('real','news_document','stock_broll','generated','gap');
exception when duplicate_object then null; end $$;

do $$ begin
  create type job_status as enum ('queued','running','succeeded','failed','paused_cap','cancelled');
exception when duplicate_object then null; end $$;

do $$ begin
  create type claim_status as enum ('confirmed','disputed','unknown');
exception when duplicate_object then null; end $$;

do $$ begin
  create type source_tier as enum ('court_record','agency_release','contemporaneous_news','other_video','reference');
exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------- users
create table if not exists users (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  display_name text,
  plan text not null default 'free',              -- key into config/plans.json
  monthly_cap_usd numeric(10,2) not null default 10.00, -- hard cap; generation pauses before exceeding
  default_tier text not null default 'standard' check (default_tier in ('free','standard','premium')),
  created_at timestamptz not null default now()
);

create table if not exists channels (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  name text,
  niche text not null,
  sub_niche text,
  audience text,
  language text not null default 'en',
  tone text,
  target_minutes int not null default 12 check (target_minutes in (8,12,20,30)),
  faceless boolean not null default true,
  competitor_urls text[] not null default '{}',   -- style reference only, never copied
  cadence text,
  youtube_channel_id text,
  created_at timestamptz not null default now()
);
create index if not exists channels_user_idx on channels(user_id);

-- Every generated kit is kept as a version; `selected` holds the user's picks.
create table if not exists channel_kits (
  id uuid primary key default gen_random_uuid(),
  channel_id uuid not null references channels(id) on delete cascade,
  version int not null default 1,
  data jsonb not null,           -- validated ChannelKit JSON
  selected jsonb not null default '{}'::jsonb,  -- {name, handle, pillars[], ...}
  created_at timestamptz not null default now(),
  unique (channel_id, version)
);

create table if not exists ideas (
  id uuid primary key default gen_random_uuid(),
  channel_id uuid not null references channels(id) on delete cascade,
  batch int not null default 1,
  title text not null,
  hook text not null,
  angle text not null,
  pillar text,
  demand_score int not null check (demand_score between 0 and 10),
  competition_score int not null check (competition_score between 0 and 10),
  feasibility_score int not null check (feasibility_score between 0 and 10), -- footage feasibility
  est_cost_usd numeric(10,2) not null default 0,
  rank_score numeric(6,2) not null default 0,
  status idea_status not null default 'suggested',
  position int not null default 0,
  rationale text,
  created_at timestamptz not null default now()
);
create index if not exists ideas_channel_idx on ideas(channel_id, status, rank_score desc);

create table if not exists projects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  channel_id uuid not null references channels(id) on delete cascade,
  idea_id uuid references ideas(id) on delete set null,
  title text not null,
  status text not null default 'pack_pending'
    check (status in ('pack_pending','pack_ready','rendering','review','approved','published','failed')),
  tier text not null default 'standard' check (tier in ('free','standard','premium')),
  target_minutes int not null default 12,
  llm_provider text,
  llm_model text,
  voice_provider text not null default 'kokoro',
  voice_id text not null default 'am_michael',
  voice_speed numeric(4,2) not null default 0.95,
  caption_mode text not null default 'burned' check (caption_mode in ('burned','sidecar','both')),
  production_pack jsonb,          -- validated ProductionPack (titles, description, tags, chapters, thumb concept...)
  selected_title text,
  description text,
  tags text[] not null default '{}',
  disclosure_line text,
  fact_check_signed_at timestamptz,
  fact_check_signed_by uuid,
  approved_at timestamptz,
  approved_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists projects_user_idx on projects(user_id, created_at desc);

create table if not exists scenes (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  idx int not null,
  title text not null,
  chapter_title text,
  draft text not null default '',          -- 60-120 word draft
  narration text not null default '',      -- full expanded narration (whole-video mode)
  narration_hash text,                     -- sha256 of narration+voice settings; drives selective re-render
  on_screen_text text[] not null default '{}',
  word_count int generated always as (coalesce(array_length(regexp_split_to_array(trim(narration), '\s+'),1),0)) stored,
  audio_path text,
  audio_hash text,                         -- narration_hash the audio was made from
  audio_duration_sec numeric(10,3),
  sentence_timings jsonb,                  -- [{text,start,end}] relative to scene start
  start_sec numeric(10,3),
  visual_path text,
  visual_hash text,
  footage_type footage_tier,
  blur_status text not null default 'pending' check (blur_status in ('pending','auto_done','needs_review','signed_off','not_needed')),
  updated_at timestamptz not null default now(),
  unique (project_id, idx)
);

-- Research: court record first, agency releases second, contemporaneous news third, other videos last.
create table if not exists research_sources (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  ref text not null,                     -- short id the agents cite, e.g. S3
  tier source_tier not null,
  title text not null,
  publisher text,
  url text,                              -- only ever filled from a retrieval result, never by an LLM
  published_at date,
  retrieved_at timestamptz not null default now(),
  retrieval_provider text,               -- courtlistener / gdelt / loc / nara / user / fixture
  excerpt text,
  unique (project_id, ref)
);

create table if not exists research_claims (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  scene_idx int,
  claim text not null,
  status claim_status not null,
  source_refs text[] not null default '{}',
  note text,
  -- A confirmed claim must cite at least one source.
  constraint confirmed_needs_source check (status <> 'confirmed' or cardinality(source_refs) > 0)
);

-- Every visual (real clip, document, stock, generated card) is an asset with provenance.
create table if not exists assets (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  scene_id uuid references scenes(id) on delete set null,
  kind text not null check (kind in ('clip','image','document','audio','music','thumbnail','caption','card')),
  tier footage_tier not null,
  storage_path text,
  title text,
  source_name text,          -- e.g. "Internet Archive", "Pexels"
  agency text,               -- original agency or publisher
  release_date date,
  source_url text,
  license license_status not null default 'Do Not Use',
  rights_checked boolean not null default false,
  rights_checked_by uuid,
  rights_checked_at timestamptz,
  rights_notes text,
  verified_real boolean not null default false,   -- shown to viewers as the real incident/person/place
  is_broll_label boolean not null default false,  -- edit-notes label "B-roll"
  graphic boolean not null default false,         -- graphic content flag; never usable as thumbnail
  selected boolean not null default false,        -- used in the timeline
  in_sec numeric(10,3), out_sec numeric(10,3),
  meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),

  -- RIGHTS RULES (enforced in the database, not just the UI)
  -- 1. Stock is never real footage and is always labelled B-roll.
  constraint stock_never_real check (tier <> 'stock_broll' or (verified_real = false and is_broll_label = true)),
  -- 2. Only tier 'real' can be verified real footage, and only with a full source record and a rights check.
  constraint verified_real_requires_source check (
    verified_real = false or (
      tier = 'real' and source_url is not null and agency is not null and release_date is not null
      and rights_checked = true and license <> 'Do Not Use')),
  -- 3. Do Not Use can never be placed on the timeline.
  constraint do_not_use_never_selected check (not (selected and license = 'Do Not Use')),
  -- 4. Real or news/document footage on the timeline must have been rights-checked.
  constraint selected_real_needs_rights check (
    not (selected and tier in ('real','news_document') and rights_checked = false)),
  -- 5. A gap is a placeholder, it has no media.
  constraint gap_has_no_media check (tier <> 'gap' or storage_path is null)
);
create index if not exists assets_project_idx on assets(project_id, scene_id);

create table if not exists footage_plan (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  scene_idx int not null,
  priority int not null,                 -- 1 real, 2 news/doc, 3 stock
  tier footage_tier not null,
  description text not null,
  search_query text not null,
  source_hint text,                      -- e.g. "City PD transparency portal; NARA catalog"
  status text not null default 'planned' check (status in ('planned','found','gap','rejected')),
  asset_id uuid references assets(id) on delete set null,
  created_at timestamptz not null default now()
);

create table if not exists foia_requests (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  request_number text,
  request_date date,
  agency text not null,
  records_requested text not null,
  status text not null default 'drafted' check (status in ('drafted','submitted','acknowledged','partial','granted','denied','appealed','closed')),
  exemption_claimed text,
  appeal_path text,
  notes text,
  created_at timestamptz not null default now()
);

-- Blur / redaction plan: auto-detected tracks and manual boxes, burned into pixels at render.
create table if not exists redaction_plan (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  asset_id uuid references assets(id) on delete cascade,
  scene_idx int,
  kind text not null check (kind in ('face','plate','manual_box','graphic_cutaway','audio_mute','audio_bleep','caption_omit','text_pii')),
  origin text not null default 'auto' check (origin in ('auto','manual','agency')),
  track_id int,
  t_start numeric(10,3) not null,
  t_end numeric(10,3) not null,
  box jsonb,            -- {x,y,w,h} normalised 0..1 (manual) or per-frame track file path in meta
  reason text,
  meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  check (t_end >= t_start)
);

create table if not exists renders (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  version int not null,
  status text not null default 'queued' check (status in ('queued','running','succeeded','failed','paused_cap','cancelled')),
  stage text,
  progress numeric(5,2) not null default 0,
  mp4_path text, srt_path text, script_path text, pack_path text, thumb_path text,
  duration_sec numeric(10,3),
  has_audio boolean,
  integrated_lufs numeric(6,2),
  ffprobe jsonb,
  estimate_usd numeric(10,4),
  error text,
  created_at timestamptz not null default now(),
  finished_at timestamptz,
  unique (project_id, version)
);

create table if not exists blur_signoffs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  render_id uuid not null references renders(id) on delete cascade,
  user_id uuid not null references users(id),
  frames_checked int not null check (frames_checked > 0),
  scrubbed_full_video boolean not null,
  notes text,
  signed_at timestamptz not null default now(),
  unique (render_id)
);

create table if not exists youtube_connections (
  user_id uuid primary key references users(id) on delete cascade,
  google_sub text,
  channel_id text,
  channel_title text,
  scopes text[] not null,
  access_token_enc text not null,   -- AES-256-GCM, key in TOKEN_ENCRYPTION_KEY (server only)
  refresh_token_enc text,
  expires_at timestamptz,
  audited_project boolean not null default false, -- true only after Google API compliance audit passes
  connected_at timestamptz not null default now()
);

create table if not exists publishes (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  render_id uuid not null references renders(id) on delete cascade,
  user_id uuid not null references users(id),
  privacy text not null default 'private' check (privacy in ('private','unlisted','public')),
  publish_at timestamptz,
  status text not null default 'queued' check (status in ('queued','uploading','uploaded','failed','manual')),
  upload_session_uri text,          -- resumable session, reused on retry (no double upload)
  bytes_sent bigint not null default 0,
  youtube_video_id text,
  youtube_url text,
  error text,
  created_at timestamptz not null default now(),
  unique (render_id)                -- one upload per render
);

create table if not exists youtube_quota (
  day date not null,
  user_id uuid not null references users(id) on delete cascade,
  units int not null default 0,
  primary key (day, user_id)
);

-- Cost ledger: every agent / provider call. idempotency_key makes retries never double-charge.
create table if not exists cost_ledger (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  project_id uuid references projects(id) on delete set null,
  render_id uuid references renders(id) on delete set null,
  stage text not null,
  agent text,
  provider text not null,
  model text,
  unit_type text not null check (unit_type in ('tokens','characters','seconds','requests','gb')),
  units_in bigint not null default 0,
  units_out bigint not null default 0,
  cost_usd numeric(12,6) not null default 0,
  duration_ms int,
  idempotency_key text not null unique,
  meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists cost_ledger_user_month on cost_ledger(user_id, created_at);

-- Estimates shown in the cost modal, kept so the ledger can be compared against them.
create table if not exists estimates (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  project_id uuid references projects(id) on delete cascade,
  action text not null,
  tier text not null,
  lines jsonb not null,
  total_usd numeric(12,4) not null,
  accepted boolean not null default false,
  created_at timestamptz not null default now()
);

-- Job queue (Postgres-backed: FOR UPDATE SKIP LOCKED + leases + retries with backoff).
create table if not exists jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  project_id uuid references projects(id) on delete cascade,
  render_id uuid references renders(id) on delete cascade,
  kind text not null,             -- channel_kit | ideas | production_pack | render_stage | publish | blur_preview
  stage text,                     -- for render_stage: research|script|voice|visuals|blur|assemble|package
  payload jsonb not null default '{}'::jsonb,
  status job_status not null default 'queued',
  priority int not null default 100,
  attempts int not null default 0,
  max_attempts int not null default 4,
  run_after timestamptz not null default now(),
  locked_by text,
  locked_until timestamptz,
  progress numeric(5,2) not null default 0,
  progress_note text,
  last_error text,
  idempotency_key text unique,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);
create index if not exists jobs_claim_idx on jobs(status, run_after, priority) where status in ('queued','running');
