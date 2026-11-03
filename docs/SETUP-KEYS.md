# Keys and accounts

Everything the app needs, what it's for, and whether there is a free path. The app **never invents output** when a key is
missing. The job stops, says which variable is missing, and charges nothing. Run `python -m ycb.main check-env` in `worker/`
to list what is missing.

| Variable | Needed for | Free path | Where to get it |
|---|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | Database, auth, storage | Supabase free plan | supabase.com → New project → Settings → API |
| `DATABASE_URL` | Worker → Postgres (queue, ledger) | same project | Supabase → Connect → **Session pooler** string (port 5432) |
| `GOOGLE_OAUTH_CLIENT_ID/SECRET` | Connecting YouTube | free | docs/GOOGLE-CLOUD-SETUP.md |
| `TOKEN_ENCRYPTION_KEY` | Encrypting YouTube tokens | — | `openssl rand -base64 32`, same in web and worker |
| `OPENAI_API_KEY` | Standard-tier agents (gpt-4o-mini), OpenAI voices | Gemini free tier / Ollama | platform.openai.com → API keys |
| `ANTHROPIC_API_KEY` | Optional agent provider | — | console.anthropic.com |
| `GEMINI_API_KEY` | Free-tier agents (Gemini 2.5 Flash) | yes (rate-limited) | aistudio.google.com → Get API key |
| `OLLAMA_URL` | Self-hosted free agents | yes | ollama.com, run `ollama pull llama3.1:8b` |
| `ELEVENLABS_API_KEY` | Premium voice | Kokoro | elevenlabs.io → Profile → API key (commercial use needs a paid plan) |
| `GOOGLE_TTS_API_KEY` | Chirp 3 HD voice | Kokoro (1M chars/month free on Google too) | Cloud console → enable Text-to-Speech API → Credentials → API key (restrict it to that API) |
| `PEXELS_API_KEY` | Generic B-roll only | free | pexels.com/api → Your API key |
| `PIXABAY_API_KEY` | Generic B-roll only | free | pixabay.com/api/docs (log in, key is shown on the page) |
| `COURTLISTENER_TOKEN` | Court records (research tier 1) | free | courtlistener.com → profile → API token |
| `NARA_API_KEY` | National Archives catalog search | free | email catalog_api@nara.gov (see catalog.archives.gov API docs) |
| `SENTRY_DSN` | Error monitoring | free plan | sentry.io → new Next.js + Python projects |

## Pexels and Pixabay

1. Create accounts, request API keys (instant), and put them in the **worker** env only. Keys are never sent to the browser.
2. Pexels asks for attribution where possible. The app writes credits for every clip into the upload pack anyway.
3. Stock is used **only** for generic B-roll. The database refuses to mark stock as real footage, and edit notes label it `B-roll`.

## LLM and voice providers

* **Free:** `GEMINI_API_KEY` (free tier, rate-limited; Google may use free-tier prompts to improve products) or `OLLAMA_URL`
  (your own machine), plus Kokoro voice (`worker/scripts/download-models.sh`, Apache-2.0, commercial use allowed).
* **Standard:** OpenAI gpt-4o-mini for all agents, OpenAI tts-1 voice. A 30-min video comes to about $0.46 (see Costs page).
* **Premium:** adds a gpt-4o polish pass and ElevenLabs Multilingual v2, about $2.72 for 30 minutes.
* Prices live in `config/pricing.json` (verified 2026-11-02). Update `last_verified` when you re-check them.

## Free voice (Kokoro) files

`worker/scripts/download-models.sh /models` downloads Kokoro, its voices, the YuNet face detector and the plate detector
(about 360 MB). The Docker image runs it at build time.
