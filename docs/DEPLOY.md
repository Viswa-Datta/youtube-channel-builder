# Deploy

Three pieces: **Supabase** (Postgres + Auth + Storage), **Vercel** (Next.js web app), **render worker** (Docker on Fly.io,
Railway or any VPS). The worker and the web app never talk to each other directly; they share the Postgres job queue.

## 1. Supabase
1. Create a project. Note the URL, anon key, service-role key and the **Session pooler** connection string.
2. Apply the migrations in order (SQL editor, or `supabase db push` with the CLI linked to this repo):
   `supabase/migrations/0001_schema.sql`, `0002_functions.sql`, `0003_rls.sql`. They are idempotent.
   **Do not** apply `supabase/local/auth_stub.sql` (local tests only).
3. Storage: the migration creates a private bucket `projects`. Raise the bucket's file-size limit to at least 2 GB
   (Storage → Settings) for 30-minute renders. (Over 50 MB per file needs a paid Supabase plan; alternative: Cloudflare R2.)
4. Auth → Providers: enable Email (magic link) and Google (see GOOGLE-CLOUD-SETUP.md §4). Auth → URL configuration:
   Site URL = your Vercel URL; redirect URL `https://YOUR_APP/auth/callback`.
5. Backups: Supabase Pro has daily backups with point-in-time recovery as an add-on; on Free, schedule `pg_dump` (below).

## 2. Web app on Vercel
1. Import the repo. **Root Directory: `web`**. Framework: Next.js. Node 20+.
2. Environment variables: everything under "Web app" in `.env.example`.
3. Deploy. `prebuild` copies `config/*.json` into the app, so pricing and plans are bundled.
4. Turn on Vercel Analytics (Project → Analytics). Add the Sentry DSN for error monitoring.
5. Check `https://YOUR_APP/api/health` — it lists which integrations are configured (names only).

## 3. Render worker on Fly.io
```bash
fly launch --no-deploy --config worker/fly.toml --dockerfile worker/Dockerfile   # once
fly volumes create ycb_work --size 20 --config worker/fly.toml
fly secrets set --config worker/fly.toml DATABASE_URL=... SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
  STORAGE_DRIVER=supabase TOKEN_ENCRYPTION_KEY=... GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... \
  OPENAI_API_KEY=... PEXELS_API_KEY=... PIXABAY_API_KEY=... COURTLISTENER_TOKEN=...
fly deploy --config worker/fly.toml --dockerfile worker/Dockerfile .              # from the repo root
fly scale count 1 --config worker/fly.toml
```
Railway / VPS: build `worker/Dockerfile` with the repo root as context, set the same env, run `python -m ycb.main worker`.
Scale horizontally by running more workers; `FOR UPDATE SKIP LOCKED` makes claims safe, and a crashed worker's lease
expires after `JOB_LEASE_SECONDS` so another worker resumes the job.

Measured on 2 vCPU: Kokoro voices about real time (30 min of narration ≈ 29 min) and the final caption burn-in takes ~15–20 min;
paid TTS providers are much faster. Use `performance-4x` for faster free renders.

## 4. End-to-end check on the deployed app
1. Sign in → New channel: "True Crime Documentary", 30 minutes → generate kit → generate ideas.
2. Pick "How a Cold Case Investigation Actually Works…" → production pack → Generate whole video.
3. When finished: Review Studio → play, captions, chapters → scrub, sign blur pass → fact-check → approve.
4. Download the MP4 and run `ffprobe -v error -show_streams -select_streams a file.mp4` to confirm an audio stream; play it in
   a desktop player (VLC) and on a phone.
5. Settings → Connect YouTube → Publish → Upload (private until audited) → confirm in YouTube Studio.

## 5. Backups
* Database: Supabase daily backups (Pro), or a nightly GitHub Action / cron:
  `pg_dump "$DATABASE_URL" -Fc -f ycb-$(date +%F).dump` → upload to R2/S3 with lifecycle rules.
* Media: renders live in the `projects` bucket. For an offsite copy, sync it nightly to R2 (`rclone sync`). Intermediate
  clips can be regenerated; final renders, captions and upload packs are the files worth backing up.
