# Test report — 30-minute True Crime test video

**Video:** "How a Cold Case Investigation Actually Works, From First Report to Final Arrest" (published title:
"How a Cold Case Is Actually Solved, Step by Step"), channel **The Quiet Case File**, niche True Crime Documentary.
**Where:** the build sandbox (2 vCPU, 7 GB RAM), real Postgres 16, real worker processes, real FFmpeg/Kokoro/YuNet/YOLO.
**Raw results:** `reports/e2e-30min.json`, `reports/e2e-rerender.json`, worker logs, `reports/contact-sheet-*.jpg`.

## Read this first: what was real and what was not
| Part | Status in this run |
|---|---|
| Database, queue, leases, retries, ledger, cap, rights rules, publish gate | **Real** (Postgres 16, migrations as shipped) |
| Narration voice | **Real**: Kokoro-82M, voice `am_michael` at 0.95×, synthesized locally, $0 |
| Rendering: cards, captions, chapters, music bed, ducking, loudness, MP4 | **Real** FFmpeg, 1920×1080 H.264 + AAC 48 kHz |
| Blur pipeline | **Real** detection + tracking + burn-in, on a separate test clip (the 30-min video had no camera footage) |
| Agent outputs (kit, 24 ideas, research, 7-scene draft, footage plan, SEO, 30-min narration) | **Fixture provider.** No LLM key existed here, so these were written in this session, put through the same pydantic validation, and run through the pipeline. Every cited source URL was checked to exist via web search. A real LLM key replaces this with live generation. |
| Footage search (archives, Pexels, Pixabay) | **Not reachable** (sandbox network policy). Every scene's real-footage need was correctly logged as a **GAP** with its search query and source hint; visuals are app-made document/chapter/timeline cards labelled as such. |
| YouTube upload | Tested against a **local fake** of Google's OAuth + resumable upload protocol. No Google account here. |
| Web UI | **Not run here** (npm blocked). Type-checked against stub declarations only. |

## Definition of done — results
| # | Test (spec §13) | Result | Evidence |
|---|---|---|---|
| 1 | New user signs in, enters True Crime Documentary, gets a channel kit, 20+ ranked ideas, picks Idea 1, gets a production pack | ✅ PASS (fixture agents) | Profile row created by the auth trigger; kit with 5 names, 4 handles, 4 pillars; **24 ideas** ranked (Idea 1 ranks first at 7.65); pack: 7 scenes, 21 claims (19 confirmed, 1 disputed, 1 unknown), 13 sources (9 agency, 2 news, 2 reference; no court record since this is a process explainer), 12 footage needs, 3 titles all under 60 chars |
| 2 | Idea expands to a full 30-minute narration with captions and chapters, not outlines | ✅ PASS | **4,376 words**, **30.22 min** of narration, 457 caption cues with real sentence timings, 7 chapters |
| 3 | Full video renders in the background, with progress, retry per stage, no double-charge or double-upload on retry | ✅ PASS | 6 stage jobs chained in Postgres; progress 0→100; voice ledger: 7 rows for 7 scenes (unique keys); upload test: 1 session, 1 video after a mid-upload failure |
| 4 | Downloaded video plays with voice, captions and visuals; ffprobe shows an audio stream (silent = fail) | ✅ PASS | ffprobe: `h264 1920x1080`, **`aac 48000 Hz stereo`**, `mov_text` captions, 7 chapters, 30:12.96. Integrated **−14.3 LUFS**, true peak **−1.1 dBTP**, LRA 2.5, silence ratio 0.0 (13 of ~85,000 audio frames below −40 dBFS). Decoded end to end by FFmpeg outside any browser; no browser TTS exists anywhere in the app. |
| 5 | Blur test: a clip with a face and a plate is blurred in every frame, including when the subject moves | ✅ PASS | Real photographed face that moves, scales 0.6→1.0, rotates ±18°, mirrors for 1.5 s (turning), is half-occluded for 0.3 s and drifts off-frame; plate drives across the frame. **Face blurred 240/240 frames, plate 212/212 visible frames** (detail energy cut ≥ 85%, worst 0.058 and 0.0999 of original). One continuous track per object, so no flicker. |
| 6 | Rights test: unverified clip cannot be marked verified real; stock is never labelled real | ✅ PASS | Database CHECK constraints reject: verified-real without source/agency/date/rights check; stock with verified_real; stock without the B-roll label; "Do Not Use" on the timeline; real footage on the timeline without a rights check. The API also rejects third-party hosts (YouTube re-uploads) as a source. |
| 7 | Failed job test: kill the worker mid-render; retry resumes without corrupting the project | ✅ PASS | Worker A was **SIGKILLed** during voice after 2 of 7 scenes. Its lease expired; worker B reclaimed the job (attempt 2, logged "lease expired (worker lost), resumed"), **skipped the 2 finished scenes** (hashes unchanged), voiced the other 5. The assemble stage then hit a real bug (below), failed twice with backoff, and succeeded on attempt 3 after the fix and a fresh worker, with no corruption. |
| 8 | Cost test: estimator shows $0 free and a paid total before generation; ledger matches after | ✅ PASS | Free **$0.00**; Standard **$0.4575**; Premium **$2.7215** for 30 min (math: 30 min × 143 wpm = 4,290 words × 6 = 25,740 voice chars). Ledger after: **$0.00** across 20 rows (12 fixture agent calls, 7 Kokoro scenes, 1 compute). Paid-path ledger math verified separately: 4,000 in + 12,000 out tokens on gpt-4o-mini recorded at exactly the price-table cost. TS estimator equals Python estimator for all 72 combinations. Hard cap: a job that would exceed the cap pauses (`paused_cap`) with no ledger rows. |
| 9 | Upload test: private upload with title, description, tags, chapters, thumbnail; or clear private-only explanation + MP4 | ✅ PASS against local fake · 👤 real channel pending | Unaudited project forced to **private** (requested "public"); title, description with `0:00` chapters, tags and thumbnail sent; quota logged; a failure after the first chunk resumed from the server's byte offset; re-running the job and inserting a second publish for the same render were both refused. UI states private-only status and offers MP4 + upload-pack download. |
| 10 | Watch once on a phone and once on a large screen | 👤 **Not done** — needs a person | Proxy checks: contact sheet across the 30 min (`reports/contact-sheet-*.jpg`) shows readable burned captions and card text; captions are 2 lines × 42 chars. |

## Also verified
* Publish gate in the database: blocked before review (missing: blur sign-off, fact-check, approval); approval blocked until
  the blur pass is signed on the latest render; checklist passes after review (title 48 chars, chapters in description,
  disclosure line, non-graphic thumbnail, footage logged, rights checked). A silent render cannot be marked succeeded.
* Missing keys: an Ideas job on Standard with no `OPENAI_API_KEY` fails once with "set OPENAI_API_KEY", creates no ideas,
  and charges nothing.
* Agents: invalid JSON gets one corrective retry, then fails; the Research Agent is refused if it cites a source the retriever
  did not return.
* Captions omit phone numbers, street addresses and named terms marked "caption_omit".
* Selective re-render: see below.

## Selective re-render (Review Studio edit → only changed scenes rebuilt)
✅ PASS (`reports/e2e-rerender.json`). After review, scene 5's narration was edited (one sentence added) and render v2 was
started. Result: **only scene 5 was re-voiced and recomposed** (`scenes_revoiced: [4]`, `scenes_recomposed: [4]`, zero-based),
the other 6 scenes' audio and visuals were reused, exactly **one** new voice ledger row was written (7 → 8), and v2 took
**24.7 min vs 90.5 min** for the full render. The v2 export again passed: audio present, −14.3 LUFS, 7 chapters, captions.
Edits also clear the fact-check and approval, so the reviewer must re-check before publishing.

A third render (v3) changed only the card style: it reused **all** narration (0 voice ledger rows added) and rebuilt the
visuals. v3 is the delivered video.

## Bugs found by these tests and fixed
1. Postgres function calls needed explicit casts (`within_cap`, `heartbeat_job`, `claim_job`, `fail_job`).
2. The music-bed `tremolo` frequency was below FFmpeg's minimum (0.08 → 0.1 Hz); found mid-render, fixed, and the stage retried.
3. The generated music bed was −55.7 LUFS, so inaudible after ducking; it is now normalized to −16 LUFS before the mix.
4. A failing **publish** job reset the render's status to "running" (found by the upload test); render state is now only
   touched by render-stage jobs.
5. Card layout let document pages and long statements run into the caption band; cards now keep content in the top 68%.
6. The thumbnail headline overlapped the case-file graphic; the headline now uses the largest size that fits in 3 lines.

## Known limitations
* Timing on 2 vCPU: voice stage 28.6 min for 30.2 min of audio (Kokoro ≈ real time); assemble about 20 min (mostly the final
  re-encode that burns in captions). The first full render took 90.5 min end to end because assemble failed twice on the
  tremolo bug before succeeding. A 4-vCPU worker or a paid TTS provider is several times faster.
* The narration was written in this session (fixture), not by the app's Script Agent; the first real run with an LLM key is
  the true test of script quality.
* No real footage could be fetched here, so the test video is documents/cards plus generated ambient music; real archive
  footage, rights checks and blur on real clips will happen on the deployed app.
* Untested here: Supabase Auth/RLS with real JWTs, Supabase Storage (incl. TUS upload), all paid provider APIs, the web UI.
