# YouTube Channel Builder

Build and run a long-form YouTube channel: niche → channel kit → ranked ideas → (you pick) → sourced research, full script,
narration, real-footage-first visuals with rights tracking, privacy blur, captions, chapters, thumbnail and SEO pack →
(you review and approve) → MP4 download and/or YouTube upload. Cost is shown before every step and recorded after it.

* **Human gates:** you pick the topic; you scrub and sign off the blur pass, sign the fact-check and approve the final video.
  Nothing is published without that.
* **No fake output:** a missing key stops the job and names the variable. Footage that can't be verified shows as a GAP with a
  search query and source hint, never as an invented clip.

## Repo layout
```
web/        Next.js 15 app (App Router, Tailwind, shadcn-style UI, Supabase Auth) — deploy to Vercel (root: web)
worker/     Python render worker (agents, TTS, footage, blur, FFmpeg, YouTube upload) — Docker → Fly.io/Railway/VPS
supabase/   SQL migrations: schema, business rules (triggers/constraints), RLS
config/     pricing.json and plans.json, shared by web and worker
fixtures/   The Quiet Case File seed project (test fixtures for the agents, sources verified)
docs/       SETUP-KEYS, GOOGLE-CLOUD-SETUP, YOUTUBE-AUDIT, DEPLOY, ARCHITECTURE, LAUNCH-CHECKLIST, TEST-REPORT
reports/    Acceptance test outputs (e2e-30min.json, worker log)
```

## Local setup
Requirements: Node 20+, Python 3.12, FFmpeg (with libass/libx264), espeak-ng, Tesseract, Postgres 15+ or the Supabase CLI.

```bash
cp .env.example .env                       # fill what you have; see docs/SETUP-KEYS.md
# Database: hosted Supabase project, or local:
supabase start && supabase db reset        # applies supabase/migrations
# Worker
cd worker && pip install -r requirements.txt && bash scripts/download-models.sh /models
python -m ycb.main check-env               # lists missing keys/models (nothing is faked)
python -m ycb.main worker
# Web
cd ../web && cp ../.env.example .env.local && npm install && npm run dev   # http://localhost:3000
```
`STORAGE_DRIVER=local` stores media under `.storage/` and serves it through `/api/files` for development.

## Tests
```bash
cd worker && python -m pytest -q tests/test_rules.py tests/test_youtube_upload.py tests/test_media.py
python tests/e2e_acceptance.py             # full 30-minute True Crime render with a mid-render worker kill (≈1.5 h on 2 vCPU)
cd ../web && npx tsx --test tests/pricing.test.ts   # TS estimator == Python estimator for every tier/length
```

## Costs (per `config/pricing.json`, verified 2026-11-02)
| Video | Free | Standard (gpt-4o-mini + OpenAI tts-1) | Premium (+ gpt-4o polish + ElevenLabs) |
|---|---|---|---|
| 10 min | $0.00 | ≈ $0.16 | ≈ $0.92 |
| 30 min | $0.00 | ≈ $0.46 | ≈ $2.72 |

30 min = 4,290 words × 6 characters = 25,740 voice characters. Free mode uses the Gemini free tier or Ollama, Kokoro voice,
free footage sources and a self-hosted render.

See [docs/DEPLOY.md](docs/DEPLOY.md) to launch and [docs/LAUNCH-CHECKLIST.md](docs/LAUNCH-CHECKLIST.md) for what's done and
what needs your accounts.
