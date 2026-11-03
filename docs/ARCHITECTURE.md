# Architecture

```
Browser ── Next.js on Vercel (UI + API routes, Supabase Auth session, RLS-scoped queries)
              │  inserts jobs (service role, after ownership + cost-modal + cap checks)
              ▼
        Supabase Postgres ── jobs (queue) · cost_ledger · projects · scenes · assets · renders · publishes …
              ▲  claim_job(): FOR UPDATE SKIP LOCKED, leases, heartbeats, retries with backoff
              │
        Render worker (Docker: Python, FFmpeg, espeak-ng, ONNX Runtime, OpenCV, Tesseract)
          ├─ agents: Channel · Ideas · Research · Script · Visual Director · Editor+SEO   (pydantic-validated JSON)
          ├─ Voice Agent: Kokoro (free) | OpenAI | ElevenLabs | Google Chirp 3 — sentence by sentence
          ├─ footage: Internet Archive · Library of Congress · NARA · Pexels · Pixabay (with provenance)
          ├─ blur: YuNet faces + YOLOv9 plates + OCR digits → tracker → burned-in blur → FFmpeg
          ├─ assemble: scene videos → narration + ducked music → loudnorm −14 LUFS → captions + chapters → MP4
          └─ publish: YouTube resumable upload (session reused on retry), thumbnails.set, quota log
              │
        Supabase Storage: projects/<id>/audio/scene-n.{wav,mp3} · clips/scene-n.mp4 · renders/v-n.mp4 ·
                          renders/thumb-v-n.jpg · captions.srt · captions.vtt · renders/{script,upload-pack}-v-n.txt
```

## Why a Postgres queue (not BullMQ/Redis, Inngest or Trigger.dev)
The spec allows Inngest, Trigger.dev or BullMQ + Redis. This build uses a Postgres queue in the same Supabase database:
* one less account and bill (Upstash's free Redis meters every command, and BullMQ polling uses them up);
* enqueueing is in the same database as the rows it refers to, and the ledger and jobs are joinable for cost reports;
* it has the properties the spec asks for: retries per stage (each stage is its own job with `max_attempts` and exponential
  backoff), leases with heartbeats so a killed worker's job is resumed by another, and idempotency keys so a stage is
  enqueued once even if it completes twice.
Swapping in BullMQ later only means replacing `worker/ycb/queue.py` and `web/lib/jobs.ts`.

## Render stages and resume
`script → voice → footage → blur → assemble → package`, each its own job. Inside a stage, work is checkpointed per scene and
keyed by content hashes (`narration_hash`, `visual_hash`), and files are written atomically (`.part` then rename). A retry
skips scenes already done. An edit re-renders only changed scenes (plus the final mux). Paid calls are skipped when output
already exists, and ledger rows are unique on `idempotency_key`, so retries never double-charge.

## Rules enforced in the database (not just the UI)
* Assets: stock is never `verified_real` and always carries the B-roll label; `verified_real` needs tier `real`, source URL,
  agency, release date, a human rights check and a usable licence; `Do Not Use` can never be on the timeline; real/news
  footage on the timeline must be rights-checked; a gap has no media.
* Research claims marked confirmed must cite a source; the Research Agent may only cite sources the retriever returned.
* A render cannot be marked succeeded without an audio stream.
* Approval requires a blur sign-off on the latest render; publishing requires the full checklist (trigger on `publishes`),
  and one publish per render (unique).
* Spend: `within_cap()` is checked before every paid step; jobs pause (`paused_cap`) instead of exceeding the cap.

## Agents
Each agent is one narrow prompt plus one pydantic schema (`worker/ycb/schemas.py`). Invalid JSON gets one corrective retry,
then the run fails and nothing downstream runs. Every call logs provider, model, tokens, cost and duration to `cost_ledger`.
The `fixture` provider exists only for tests (`ALLOW_FIXTURES=true`). It replays pre-written JSON through the same
validation, so the pipeline can be tested end to end without an LLM key.
