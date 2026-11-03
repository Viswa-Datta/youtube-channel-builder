# Launch checklist

Legend: ✅ done and tested in the build environment · 🟡 built, not run here (needs an account or `npm install`) · 👤 your manual action

## Build environment limits (read first)
The build sandbox had **no access to npm or PyPI** (blocked by network policy) and **no accounts or API keys**. So:
* The **worker, database and media pipeline were run and tested for real** here: Postgres 16, FFmpeg, Kokoro, YuNet, YOLO plates.
* The **Next.js web app was written but never installed or built here.** It was type-checked only against stub
  declarations of its dependencies. The first `npm install && npm run build` happens in CI or on Vercel. Expect to fix a few
  compile errors there.
* LLM steps for the test video used the **fixture provider** (pre-written JSON put through the same schema validation and
  pipeline). No LLM key was available, and the app refuses to invent output.

## Repo and config
- ✅ Repo structure, README with local setup, `.env.example` with every key named, no real keys committed
- ✅ Shared `config/pricing.json` (prices verified 2026-11-02) and `config/plans.json` (Free/Creator/Pro as config)
- ✅ CI workflow (`.github/workflows/ci.yml`): worker tests on Postgres, web typecheck + estimator parity + `next build`
- 🟡 CI run on GitHub (push the repo; first web build will surface any type errors the stub check could not see)

## Database (Supabase)
- ✅ Migrations apply cleanly and re-apply idempotently on Postgres 16
- ✅ All required tables: users, channels, channel_kits, ideas, projects, scenes, assets, renders, publishes, cost_ledger, jobs,
  footage_plan, research_sources, redaction_plan (+ research_claims, foia_requests, blur_signoffs, youtube_connections,
  youtube_quota, estimates)
- ✅ Rights rules, publish gate, silent-render block, approval gate, spend cap enforced in SQL (tested)
- 🟡 RLS policies (written; not exercised against real Supabase Auth JWTs here)
- 👤 Create Supabase project, apply migrations, enable Email + Google auth, set bucket size limit (docs/DEPLOY.md §1)

## Worker
- ✅ Postgres job queue with leases, heartbeats, per-stage retries with backoff, idempotency keys (kill test passed)
- ✅ Seven agents with pydantic-validated JSON and ledger logging (validation and retry tested with a mocked provider)
- ✅ Kokoro free voice; 30-minute narration rendered here
- ✅ Blur pipeline: detect + track + burn-in (face and plate blurred in every frame of the moving/turning test clip)
- ✅ Export: narration audio stream, −14 LUFS, chapters, burned and sidecar captions, .srt, script .txt, upload pack .txt
- 🟡 OpenAI / Anthropic / Gemini / Ollama / ElevenLabs / Google TTS calls (implemented to their documented APIs; untested without keys)
- 🟡 Footage search: Internet Archive, Library of Congress, NARA, Pexels, Pixabay, CourtListener, GDELT (no outbound network here)
- 🟡 Supabase Storage driver incl. TUS resumable upload for large files (written to Supabase's documented protocol; untested)
- 👤 Deploy the worker to Fly.io/Railway/VPS with secrets (docs/DEPLOY.md §3)

## Web app
- 🟡 Pages: Dashboard, Channel Kit, Idea Board, Production Pack, Review Studio, Cost and Subscriptions, Publish, Settings, legal pages
- 🟡 Cost modal before every generation, Free/Standard/Premium re-pricing, hard cap, "videos like this cost $X last time"
- ✅ Estimator parity: the TypeScript estimator equals the Python estimator for all 72 action/tier/length combinations
- 🟡 Mobile layout (phone nav bar, one-column kanban with column tabs, stacked studio); not visually tested here
- 👤 Deploy to Vercel with Root Directory `web` and env vars (docs/DEPLOY.md §2)

## Google / YouTube (all manual; Google controls these)
- 👤 Google Cloud project, enable YouTube Data API v3, OAuth consent screen, Web client, redirect URIs (docs/GOOGLE-CLOUD-SETUP.md)
- 👤 OAuth verification for the sensitive scopes (demo video, domain verification)
- 👤 YouTube API compliance audit to allow non-private uploads; then set `YOUTUBE_API_AUDITED=true` (docs/YOUTUBE-AUDIT.md)
- ✅ Upload logic tested against a local fake of Google's resumable protocol (private-only, resume after failure, no duplicates)
- 👤 Real upload to a test channel (needs the above)

## Provider keys (stop-and-list; nothing was invented)
- 👤 `OPENAI_API_KEY` (Standard) or `GEMINI_API_KEY` / `OLLAMA_URL` (Free) — at least one LLM is required for real generation
- 👤 `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `COURTLISTENER_TOKEN` (free), `NARA_API_KEY` (optional)
- 👤 `ELEVENLABS_API_KEY` / `GOOGLE_TTS_API_KEY` (optional paid voices)
- 👤 `TOKEN_ENCRYPTION_KEY`, `GOOGLE_OAUTH_CLIENT_ID/SECRET`, Supabase keys, `SENTRY_DSN`

## Legal, SEO, monitoring, backups
- 🟡 Privacy policy, terms (users responsible for YouTube ToS / Community Guidelines compliance), data-deletion page and flow (template text)
- 👤 Have the legal pages reviewed for your jurisdiction; fill operator name and contact
- 🟡 robots.txt, sitemap, metadata; Sentry (web + instrumentation) and Vercel Analytics wired, inert until DSN/enabled
- 👤 Backups: Supabase daily backups (Pro) or the `pg_dump` + `rclone` jobs in docs/DEPLOY.md §5
- 👤 Stripe: not built (spec: only if launching paid tiers). Plans are config; add Stripe price IDs when you charge

## Before calling it launched (on the deployed app)
- 👤 Run the full 30-minute True Crime flow on the deployed app with a real LLM key (docs/DEPLOY.md §4)
- 👤 Watch the rendered video once on a phone and once on a large screen (captions, loudness, clip relevance)
- 👤 Upload to a test channel as private, check title, description, tags, chapters, thumbnail
