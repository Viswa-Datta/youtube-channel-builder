"""Whole Video Generator: script -> voice -> footage -> blur -> assemble -> package.

Each stage is its own job (retries + backoff per stage). Work inside a stage is checkpointed per scene:
outputs are written atomically and keyed by content hashes, so a retry after a crash (or a re-render after
an edit) only redoes what is missing or changed. Paid calls are skipped when their output already exists,
and ledger rows are idempotent, so retries never double-charge.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from .. import db, ledger, pricing, queue
from ..agents import core as agents
from ..config import settings
from ..footage import rights, sources
from ..media import blur, captions, cards, compose, maps
from ..media.ff import acceptance, duration
from ..providers import tts
from ..storage import project_key, store

STAGES = ["script", "voice", "footage", "blur", "assemble", "package"]
WEIGHT = {"script": 10, "voice": 35, "footage": 5, "blur": 10, "assemble": 35, "package": 5}
CARD_STYLE_VERSION = "motion-v2"


def sha(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


class Ctx:
    def __init__(self, job: dict, hb: queue.Heartbeat):
        self.job, self.hb = job, hb
        self.render = db.q1("select * from renders where id=%s", (job["render_id"],))
        self.project = db.q1("select * from projects where id=%s", (self.render["project_id"],))
        self.pid = str(self.project["id"])
        self.uid = str(self.project["user_id"])
        self.rid = str(self.render["id"])
        self.work = settings.work_dir / self.pid
        self.work.mkdir(parents=True, exist_ok=True)
        self.prov, self.model, self.fx = agents.llm_choice(
            {"llm_provider": self.project["llm_provider"], "llm_model": self.project["llm_model"]}, self.project["tier"])

    def scenes(self) -> list[dict]:
        return db.q("select * from scenes where project_id=%s order by idx", (self.pid,))

    def progress(self, stage: str, frac: float, note: str = "") -> None:
        if hasattr(self.hb, "check"):
            self.hb.check()  # stops here within seconds of the user pressing Cancel
        done = sum(WEIGHT[s] for s in STAGES[:STAGES.index(stage)])
        total = done + WEIGHT[stage] * max(0.0, min(1.0, frac))
        db.x("update renders set stage=%s, progress=%s, status='running' where id=%s and status <> 'cancelled'",
             (stage, round(total, 2), self.rid))
        self.hb.update(progress=round(frac * 100, 1), note=note)

    def local(self, key: str) -> Path:
        """Fetch a stored object into the work dir (cached)."""
        p = self.work / "cache" / key.replace("/", "__")
        if not p.exists():
            store().get(key, p)
        return p


# ------------------------------------------------------------------ stage: script
def stage_script(c: Ctx) -> None:
    scenes = c.scenes()
    total_words = pricing.words_for(c.project["target_minutes"])
    draft_words = [max(1, len(s["draft"].split())) for s in scenes]
    claims = db.q("select * from research_claims where project_id=%s order by claim", (c.pid,))
    todo = [s for s in scenes if not s["narration"].strip()]
    if todo:
        est = pricing.estimate("whole_video", c.project["tier"], c.project["target_minutes"], len(scenes))
        script_cost = sum(l["cost_usd"] for l in est["lines"] if l["stage"].startswith("Script")) * len(todo) / len(scenes)
        ledger.ensure_within_cap(c.uid, script_cost, "script expansion")
    for k, s in enumerate(scenes):
        if s["narration"].strip():
            continue  # resume: already expanded
        target = int(round(total_words * draft_words[s["idx"]] / sum(draft_words)))
        sc = [cl for cl in claims if cl["scene_idx"] in (None, s["idx"])]
        text = agents.expand_scene(c.project, s, target, sc, c.prov, c.model, c.fx, c.uid, c.rid)
        db.x("update scenes set narration=%s, updated_at=now() where id=%s", (text, s["id"]))
        c.progress("script", (k + 1) / len(scenes), f"scene {s['idx']} expanded")
    # Motion graphics: planned on the final narration so each animation lands on the sentence it explains.
    for s in c.scenes():
        if s.get("graphics") is None and s["narration"].strip():
            sc = [cl["claim"] for cl in claims if cl["scene_idx"] in (None, s["idx"])]
            g = agents.plan_graphics(c.project, s, sc, c.prov, c.model, c.fx, c.uid, c.rid)
            db.x("update scenes set graphics=%s where id=%s", (db.jsonb(g), s["id"]))
            c.progress("script", 1.0, f"scene {s['idx']}: {len(g)} animations planned")
    words = sum(len(s["narration"].split()) for s in c.scenes())
    if words < 0.85 * total_words:
        raise agents.BusinessRuleViolation(f"narration is {words} words; {c.project['target_minutes']} min needs ~{total_words}")


# ------------------------------------------------------------------ stage: voice (Voice Agent)
def stage_voice(c: Ctx) -> None:
    p = c.project
    if p["voice_provider"] != "kokoro" and settings.missing_for(p["voice_provider"]) and not c.fx:
        # A paid voice was picked but its key is not set up: use the free built-in voice instead of failing.
        db.x("update projects set voice_provider='kokoro', voice_id='am_michael' where id=%s", (c.pid,))
        p = c.project = {**p, "voice_provider": "kokoro", "voice_id": "am_michael"}
        c.progress("voice", 0.0, "paid voice has no key set up: using the free voice")
    scenes = c.scenes()
    if any(len(s["narration"].split()) < 20 for s in scenes):
        # The narration was cleared after the script step (e.g. the length was changed): write it again first,
        # never record silence.
        c.progress("voice", 0.0, "narration missing: writing the script again first")
        stage_script(c)
        scenes = c.scenes()
    remaining_chars = 0
    for s in scenes:
        nh = sha(s["narration"], p["voice_provider"], p["voice_id"], float(p["voice_speed"]))
        if s["narration_hash"] != nh:
            db.x("update scenes set narration_hash=%s where id=%s", (nh, s["id"]))
            s["narration_hash"] = nh
        if s["audio_hash"] != nh:
            remaining_chars += len(s["narration"])
    cost = pricing.tts_cost(p["voice_provider"], p["voice_id"] if p["voice_provider"] == "kokoro" else _tts_model(p), remaining_chars)
    ledger.ensure_within_cap(c.uid, cost, "voiceover")
    t0 = 0.0
    for k, s in enumerate(scenes):
        nh = s["narration_hash"]
        key_wav = project_key(c.pid, "audio", f"scene-{s['idx']}.wav")
        if s["audio_hash"] == nh and store().exists(key_wav):
            dur = float(s["audio_duration_sec"])
        else:
            wav = c.work / "audio" / f"scene-{s['idx']}.wav"
            started = time.time()
            sents, chars, dur = tts.synth_scene(
                p["voice_provider"], p["voice_id"], float(p["voice_speed"]), s["narration"], wav, model=_tts_model(p),
                on_progress=lambda f, k=k: c.progress("voice", (k + f) / len(scenes), f"scene {s['idx']}"))
            mp3 = wav.with_suffix(".mp3")
            compose.run(["-i", str(wav), "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)])
            store().put(wav, key_wav, "audio/wav")
            store().put(mp3, project_key(c.pid, "audio", f"scene-{s['idx']}.mp3"), "audio/mpeg")
            ledger.record(user_id=c.uid, project_id=c.pid, render_id=c.rid, stage="voice", agent="voice",
                          provider=p["voice_provider"], model=_tts_model(p) or p["voice_id"], unit_type="characters",
                          units_in=chars, cost_usd=pricing.tts_cost(p["voice_provider"], p["voice_id"] if p["voice_provider"] == "kokoro" else _tts_model(p), chars),
                          duration_ms=int((time.time() - started) * 1000),
                          idempotency_key=f"voice:{c.pid}:{s['idx']}:{nh}", meta={"voice": p["voice_id"], "seconds": round(dur, 2)})
            db.x("""update scenes set audio_path=%s, audio_hash=%s, audio_duration_sec=%s, sentence_timings=%s, updated_at=now()
                    where id=%s""", (key_wav, nh, dur, db.jsonb([s_.__dict__ for s_ in sents]), s["id"]))
        db.x("update scenes set start_sec=%s where id=%s", (t0, s["id"]))
        t0 += dur
        c.progress("voice", (k + 1) / len(scenes), f"scene {s['idx']} voiced")


def _tts_model(p: dict) -> str | None:
    return {"openai": "tts-1", "elevenlabs": "eleven_multilingual_v2", "google": "chirp3-hd"}.get(p["voice_provider"])


# ------------------------------------------------------------------ stage: footage (Visual Director execution)
MAX_YT_REQUESTS = 8      # help requests from YouTube finds per project (don't bury the user)
MAX_FIND_REQUESTS = 3    # "we couldn't find it, do you know where it is?" requests per project


def _scene_for(text: str, scenes: list[dict]) -> dict:
    """Best scene for a found item: most shared distinctive words with the scene's title and draft."""
    from ..footage.discovery import _words
    w = _words(text)
    return max(scenes, key=lambda s: (len(w & _words(s["title"] + " " + (s["draft"] or ""))), -s["idx"]))


def _help(c: Ctx, scene: dict | None, kind: str, title: str, instructions: str, **kw) -> bool:
    """Insert a help request once (deduplicated by kind + source_url or title)."""
    key = kw.get("source_url") or title
    if db.q1("select 1 from help_requests where project_id=%s and kind=%s and coalesce(source_url,title)=%s", (c.pid, kind, key)):
        return False
    db.x("""insert into help_requests (project_id, scene_id, kind, title, instructions, source_url, source_name, agency,
            release_date, start_sec, end_sec, license_hint, rights_note)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
         (c.pid, scene["id"] if scene else None, kind, title[:300], instructions, kw.get("source_url"), kw.get("source_name"),
          kw.get("agency"), _d(kw.get("release_date")), kw.get("start_sec"), kw.get("end_sec"), kw.get("license_hint"),
          kw.get("rights_note")))
    return True


def _candidate(c: Ctx, scene: dict, tier: str, kind: str, title: str, source_name: str, source_url: str, license: str,
               rights_note: str, agency: str | None = None, release_date: str | None = None, meta: dict | None = None) -> None:
    if db.q1("select 1 from assets where project_id=%s and source_url=%s and scene_id=%s", (c.pid, source_url, scene["id"])):
        return
    db.x("""insert into assets (project_id, scene_id, kind, tier, title, source_name, agency, release_date, source_url,
            license, rights_notes, is_broll_label, selected, meta)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false,%s)""",
         (c.pid, scene["id"], kind, tier, title[:300], source_name, agency, _d(release_date), source_url, license,
          rights_note, tier == "stock_broll", db.jsonb(meta or {})))


def stage_footage(c: Ctx) -> str | None:
    """Find footage for every scene. Downloads only where the source allows it; everything the app may not fetch
    itself (YouTube uploads, unknown locations) becomes a help request. Returns "wait" to pause at the Footage Desk."""
    if settings.footage_mode != "assisted":
        return stage_footage_auto(c)
    from ..footage import discovery
    scenes = c.scenes()
    plan = db.q("select * from footage_plan where project_id=%s order by scene_idx, priority", (c.pid,))
    idea = db.q1("select * from ideas where id=%s", (c.project["idea_id"],)) if c.project["idea_id"] else None
    case = (idea or {}).get("case_info") or {}
    agency = case.get("agency")
    yt_count = db.q1("select count(*) n from help_requests where project_id=%s and kind='save_clip'", (c.pid,))["n"]

    # 1) official videos the idea check already found
    for e in (idea or {}).get("footage_evidence") or []:
        if e.get("official") and e.get("source") == "YouTube" and yt_count < MAX_YT_REQUESTS:
            sc = _scene_for(e["title"], scenes)
            yt_count += _help(c, sc, "save_clip", e["title"], _yt_instructions(e), source_url=e["url"], source_name="YouTube",
                              agency=e.get("channel") or agency, release_date=e.get("published"),
                              license_hint="Public Record with release terms", rights_note=e.get("rights_note"))

    for k, s in enumerate(scenes):
        needs = [n for n in plan if n["scene_idx"] == s["idx"]]
        for n in needs:
            if n["status"] in ("found", "gap", "rejected"):
                continue
            q = n["search_query"]
            found = 0
            try:
                if n["tier"] == "real":
                    if yt_count < MAX_YT_REQUESTS:
                        yq = q if not agency or agency.lower() in q.lower() else f"{agency} {q}"
                        for e in discovery.youtube(yq, 6, agency):
                            if e.official and discovery.FOOTAGE_RE.search(e.title) and yt_count < MAX_YT_REQUESTS:
                                if _help(c, s, "save_clip", e.title, _yt_instructions(e.dict()), source_url=e.url, source_name="YouTube",
                                         agency=e.channel, release_date=e.published, license_hint="Public Record with release terms",
                                         rights_note=e.rights_note):
                                    yt_count += 1
                                    found += 1
                    for cand in sources.search_scene(dict(n)):
                        if cand.license != "Do Not Use":
                            _candidate(c, s, "real", "clip", cand.title, cand.source_name, cand.source_url, cand.license,
                                       cand.rights_note, cand.agency, cand.release_date,
                                       {"media_url": cand.media_url, "search_query": q, **cand.meta})
                            found += 1
                    for e in discovery.wikimedia_commons(q, 4, "video"):
                        if e.license != "Do Not Use" and e.media_url:
                            _candidate(c, s, "real", "clip", e.title, e.source, e.url, e.license, e.rights_note, e.channel,
                                       e.published, {"media_url": e.media_url, "search_query": q, **e.meta})
                            found += 1
                elif n["tier"] == "news_document":
                    for e in discovery.wikimedia_commons(q, 5, "image"):
                        if e.license != "Do Not Use" and e.media_url:
                            _candidate(c, s, "news_document", "image", e.title, e.source, e.url, e.license, e.rights_note,
                                       e.channel, e.published, {"media_url": e.media_url, "search_query": q, **e.meta})
                            found += 1
                else:
                    for cand in sources.search_scene(dict(n))[:4]:
                        _candidate(c, s, "stock_broll", "clip", cand.title, cand.source_name, cand.source_url, cand.license,
                                   cand.rights_note, cand.agency, None, {"media_url": cand.media_url, "duration": cand.duration,
                                                                         "search_query": q})
                        found += 1
            except Exception as ex:  # noqa: BLE001 - a flaky API must not kill the render; it becomes a gap
                db.x("update footage_plan set source_hint=coalesce(source_hint,'') || %s where id=%s", (f" [search error: {str(ex)[:120]}]", n["id"]))
            db.x("update footage_plan set status=%s where id=%s", ("found" if found else "gap", n["id"]))

        # Stock B-roll is generic and licensed: auto-select one if the scene has nothing else yet.
        if not db.q1("select 1 from assets where scene_id=%s and selected and kind in ('clip','image')", (s["id"],)):
            stock = db.q1("""select * from assets where scene_id=%s and tier='stock_broll' and license='Licensed'
                             and meta->>'media_url' is not null order by created_at limit 1""", (s["id"],))
            if stock:
                _download(c, stock)
                db.x("update assets set selected=true where id=%s", (stock["id"],))
        db.x("delete from assets where scene_id=%s and tier in ('generated','gap') and kind='card'", (s["id"],))
        db.x("""insert into assets (project_id, scene_id, kind, tier, title, source_name, license, selected, rights_notes)
                values (%s,%s,'card','generated',%s,'YouTube Channel Builder','Licensed',true,'Made by the app for this video')""",
             (c.pid, s["id"], f"Maps, timeline and document graphics, scene {s['idx']}"))
        c.progress("footage", (k + 1) / len(scenes), f"scene {s['idx']}")

    # 2) real-footage needs nothing could satisfy: record a GAP and (for the most important ones) ask the user.
    find_count = db.q1("select count(*) n from help_requests where project_id=%s and kind='find_source'", (c.pid,))["n"]
    for n in db.q("select * from footage_plan where project_id=%s and tier='real' and status='gap' order by priority, scene_idx", (c.pid,)):
        sc = next(x for x in scenes if x["idx"] == n["scene_idx"])
        has_real = db.q1("""select 1 from assets where scene_id=%s and tier='real'
                            union select 1 from help_requests where scene_id=%s and kind='save_clip'""", (sc["id"], sc["id"]))
        if has_real:
            continue
        db.x("""insert into assets (project_id, scene_id, kind, tier, title, license, selected, meta)
                values (%s,%s,'card','gap',%s,'Do Not Use',false,%s)""",
             (c.pid, sc["id"], f"GAP: {n['description']}", db.jsonb({"search_query": n["search_query"], "source_hint": n["source_hint"]})))
        if find_count < MAX_FIND_REQUESTS:
            find_count += _help(c, sc, "find_source", f"Find: {n['description']}",
                                "The app could not find an official release for this. If you know where the agency, court or "
                                f"archive published it, save the original file and upload it here. Where to look: "
                                f"{n['source_hint'] or 'the agency transparency portal or the court website'}. "
                                "Otherwise press Skip: this scene will use maps, documents and timeline graphics instead.")

    waiting = db.q1("""select (select count(*) from help_requests where project_id=%s and status='open') +
                              (select count(*) from assets where project_id=%s and tier in ('real','news_document')
                                 and not rights_checked and license <> 'Do Not Use' and coalesce(meta->>'dismissed','') = '') n""",
                    (c.pid, c.pid))["n"]
    return "wait" if waiting else None


AUTO_MAX_VIDEOS, AUTO_MAX_IMAGES, AUTO_MAX_STOCK = 4, 10, 3


def _insert_free_asset(c: Ctx, s: dict, e, q: str) -> str:
    lic = "Public Domain" if e.license == "Public Domain" or "public" in (e.meta.get("license_short") or "").lower() \
        or "cc0" in (e.meta.get("license_short") or "").lower() else "Licensed"
    video = e.media_type == "video"
    return db.q1("""insert into assets (project_id, scene_id, kind, tier, title, source_name, agency, release_date, source_url,
            license, rights_notes, rights_checked, rights_checked_at, selected, meta)
            values (%s,%s,%s,'real',%s,%s,%s,%s,%s,%s,%s,true,now(),true,%s) returning id""",
                 (c.pid, s["id"], "clip" if video else "image", e.title[:300], e.source, (e.channel or e.source)[:200],
                  _d(e.published), e.url, lic,
                  f"Auto-checked from the source's licence metadata: {e.meta.get('license_short') or e.license}. {e.rights_note}"[:1900],
                  db.jsonb({"media_url": e.media_url, "auto_rights": True, "search_query": q,
                            **{k2: v for k2, v in e.meta.items() if k2 in ("license_short", "license_url", "nasa_manifest", "identifier",
                                                                         "collection", "thumb_url")}})))["id"]


SHOT_SEARCHES_PER_SCENE = 8


def _generate_asset(c: Ctx, s: dict, kind: str, prompt: str, gen: dict) -> dict | None:
    """Make an AI scale-model illustration (image, or a short clip started from one) for a beat; None when
    generation is unavailable (no paid tier) or the model declines. Stored like any other asset, labelled as AI."""
    from ..providers import genmedia
    if gen.get("off"):
        return None
    try:
        h = sha(c.pid, s["idx"], kind, prompt)[:16]
        img = genmedia.image(prompt, user_id=c.uid, project_id=c.pid, render_id=c.rid, idem=f"gen:{h}:img")
        if kind == "video" and gen["videos"] > 0 and settings.gen_media == "mixed":
            data = genmedia.video(prompt, settings.gen_video_seconds, user_id=c.uid, project_id=c.pid, render_id=c.rid,
                                  idem=f"gen:{h}:vid", first_frame=img)
            ext, mime, akind = "mp4", "video/mp4", "clip"
            gen["videos"] -= 1
        else:
            data, ext, mime, akind = img, "png", "image/png", "image"
        gen["images"] -= 1
    except genmedia.GenUnavailable as e:
        gen["off"] = True  # no paid tier / model not offered: stop trying for this video
        c.progress("footage", 0.6, f"AI illustrations skipped: {str(e)[:120]}")
        return None
    except Exception:  # noqa: BLE001 - a declined or failed generation just leaves the beat to real media
        return None
    out = c.work / "gen" / f"{h}.{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    key = store().put(out, project_key(c.pid, "generated", f"{h}.{ext}"), mime)
    row = db.q1("""insert into assets (project_id, scene_id, kind, tier, title, source_name, license, rights_notes,
                   rights_checked, rights_checked_at, selected, storage_path, meta)
                   values (%s,%s,%s,'generated',%s,'AI illustration (Google Gemini)','Licensed',%s,true,now(),true,%s,%s)
                   returning id, kind, title""",
                (c.pid, s["id"], akind, f"AI illustration: {prompt[:250]}",
                 "Generated for this video with Google Gemini; labelled on screen as an illustration.", key,
                 db.jsonb({"ai": True, "prompt": prompt, "auto_rights": True,
                           **({"blurred_key": key} if akind == "clip" else {})})))
    return row


def plan_scene_shots(c: Ctx, s: dict, used_urls: set, used_photos: set, clip_uses: dict, gen: dict | None = None) -> int:
    """Sentence-level shot list: the Shot List Agent picks, for every beat of narration, the photo or clip that shows
    what is being said, searches for a better one when nothing fits, and adds a short factual label. Photos are used
    once per video, clips at most twice. Returns the number of beats that have a matching picture."""
    from ..footage import discovery
    sents = s.get("sentence_timings") or []
    if not sents:
        return 0
    if s.get("shots") is not None and s.get("shots_hash") == s["narration_hash"]:
        for sh in s["shots"]:
            if sh.get("asset_id"):
                (used_photos.add(sh["asset_id"]) if sh.get("kind") == "image" else
                 clip_uses.__setitem__(sh["asset_id"], clip_uses.get(sh["asset_id"], 0) + 1))
        return sum(1 for sh in s["shots"] if sh.get("asset_id"))
    beats = agents.make_beats(sents)
    rows = db.q("""select id, kind, title from assets where scene_id=%s and selected and kind in ('clip','image')
                   and tier in ('real','news_document','stock_broll') order by created_at""", (s["id"],))
    items = [{"id": f"A{i + 1}", "kind": "photo" if r["kind"] == "image" else "clip", "title": r["title"] or "", "row": r}
             for i, r in enumerate(rows) if not (r["kind"] == "image" and str(r["id"]) in used_photos)]
    gen = gen if gen is not None else {"off": True, "images": 0, "videos": 0}
    per_img = 0 if gen.get("off") else min(gen["images"], 3)
    per_vid = 0 if gen.get("off") or settings.gen_media != "mixed" else min(gen["videos"], 1)
    plan = agents.plan_shots(c.project, s, beats, items, c.prov, c.model, c.fx, c.uid, c.rid, per_img, per_vid)
    made_img = made_vid = 0
    by_id = {it["id"]: it["row"] for it in items}
    case = _case(c)
    subj_words = discovery._words(case.get("case_name") or c.project["title"])
    shots, searches = [], 0
    for b in beats:
        sh = next((x for x in (plan.shots if plan else []) if x.beat == b["beat"]), None)
        row = by_id.get(sh.asset) if sh and sh.asset else None
        if row and ((row["kind"] == "image" and str(row["id"]) in used_photos) or
                    (row["kind"] == "clip" and clip_uses.get(str(row["id"]), 0) >= 2)):
            row = None
        if row is None and sh and sh.generate and sh.prompt and not gen.get("off") and made_img < per_img + per_vid:
            want = "video" if sh.generate == "video" and made_vid < per_vid else "image"
            row = _generate_asset(c, s, want, sh.prompt, gen)
            if row is not None:
                made_img += 1
                made_vid += row["kind"] == "clip"
        if row is None and sh and sh.query and searches < SHOT_SEARCHES_PER_SCENE:
            searches += 1
            q = sh.query
            try:
                found = discovery.free_media(q, 6, discovery._words(q) & subj_words or discovery._words(q))
            except Exception:  # noqa: BLE001
                found = []
            for e in found:
                if e.url in used_urls:
                    continue
                aid = _insert_free_asset(c, s, e, q)
                used_urls.add(e.url)
                row = {"id": aid, "kind": "clip" if e.media_type == "video" else "image", "title": e.title}
                break
        if row is not None:
            if row["kind"] == "image":
                used_photos.add(str(row["id"]))
            else:
                clip_uses[str(row["id"])] = clip_uses.get(str(row["id"]), 0) + 1
        is_ai = bool(row) and str(row.get("title") or "").startswith("AI illustration")
        label = (sh.label if sh else "")[:40]
        if is_ai:  # AI scenes are always marked as illustrations on screen
            label = f"Illustration · {label}" if label else "AI illustration"
        shots.append({"start": round(b["start"], 2), "end": round(b["end"], 2),
                      "asset_id": str(row["id"]) if row else None, "kind": row["kind"] if row else None,
                      "label": label[:52], "ai": is_ai})
    db.x("update scenes set shots=%s, shots_hash=%s where id=%s", (db.jsonb(shots), s["narration_hash"], s["id"]))
    return sum(1 for x in shots if x["asset_id"])


def stage_footage_auto(c: Ctx) -> None:
    """Fully automatic footage: only media whose licence the source itself publishes as public domain, CC0 or CC BY
    (or free stock licences) is fetched and placed, each with source, author and licence logged for the credits.
    Nothing is downloaded from YouTube, TV or news sites, and the user is never asked to fetch anything.
    The final human review (Review Studio + publish checklist) shows every item before anything is published."""
    from ..footage import discovery
    scenes = c.scenes()
    plan = db.q("select * from footage_plan where project_id=%s order by scene_idx, priority", (c.pid,))
    case = _case(c)
    subject = case.get("case_name") or c.project["title"]
    subj_words = discovery._words(subject)
    # Every item is used at most once per video; anything already found (selected or not) is never inserted again.
    used = {r["source_url"] for r in db.q("select source_url from assets where project_id=%s and source_url is not null", (c.pid,))}
    for k, s in enumerate(scenes):
        needs = [n for n in plan if n["scene_idx"] == s["idx"]]
        # items whose download failed earlier (e.g. disk full, timeout) are retried instead of searched again
        retry = db.q("""select id, meta from assets where scene_id=%s and not selected and (meta->>'auto_rights')::boolean
                        and meta ? 'download_error' and coalesce(meta->>'dismissed','') = ''
                        and coalesce((meta->>'retries')::int, 0) < 2 order by created_at""", (s["id"],))
        for a in retry[:AUTO_MAX_VIDEOS + AUTO_MAX_IMAGES]:
            meta = {k2: v for k2, v in a["meta"].items() if k2 != "download_error"}
            meta["retries"] = int(a["meta"].get("retries") or 0) + 1
            db.x("update assets set selected=true, meta=%s where id=%s", (db.jsonb(meta), a["id"]))
        have = db.q1("""select count(*) filter (where kind='clip' and tier<>'stock_broll') v, count(*) filter (where kind='image') i,
                        count(*) filter (where tier='stock_broll') st from assets where scene_id=%s and selected""", (s["id"],))
        nv, ni, nst = have["v"], have["i"], have["st"]
        queries = [n["search_query"] for n in needs if n["tier"] != "stock_broll"]
        queries += [f"{subject} {w}" for w in [s["title"]]]  # scene-specific fallback
        queries.append(subject)
        for q in dict.fromkeys(queries):
            if nv >= AUTO_MAX_VIDEOS and ni >= AUTO_MAX_IMAGES:
                break
            words = discovery._words(q) | subj_words
            try:
                found = discovery.free_media(q, 8, words & subj_words or discovery._words(q))
            except Exception:  # noqa: BLE001
                found = []
            for e in found:
                if e.url in used:
                    continue
                video = e.media_type == "video"
                if (video and nv >= AUTO_MAX_VIDEOS) or (not video and ni >= AUTO_MAX_IMAGES):
                    continue
                lic = "Public Domain" if e.license == "Public Domain" or "public" in (e.meta.get("license_short") or "").lower() \
                    or "cc0" in (e.meta.get("license_short") or "").lower() else "Licensed"
                db.x("""insert into assets (project_id, scene_id, kind, tier, title, source_name, agency, release_date, source_url,
                        license, rights_notes, rights_checked, rights_checked_at, selected, meta)
                        values (%s,%s,%s,'real',%s,%s,%s,%s,%s,%s,%s,true,now(),true,%s)""",
                     (c.pid, s["id"], "clip" if video else "image", e.title[:300], e.source, (e.channel or e.source)[:200],
                      _d(e.published), e.url, lic,
                      f"Auto-checked from the source's licence metadata: {e.meta.get('license_short') or e.license}. {e.rights_note}"[:1900],
                      db.jsonb({"media_url": e.media_url, "auto_rights": True, "search_query": q,
                                **{k2: v for k2, v in e.meta.items() if k2 in ("license_short", "license_url", "nasa_manifest", "identifier",
                                                                             "collection", "thumb_url")}})))
                used.add(e.url)
                nv += video
                ni += not video
        # generic B-roll from free stock libraries (only if keys are set) when real footage is thin
        if nv < 3 and nst < AUTO_MAX_STOCK:
            for n in [n for n in needs if n["tier"] == "stock_broll"] or [{"search_query": s["title"], "tier": "stock_broll"}]:
                for cand in sources.search_scene(dict(n))[:AUTO_MAX_STOCK - nst]:
                    if cand.source_url in used or not cand.media_url:
                        continue
                    db.x("""insert into assets (project_id, scene_id, kind, tier, title, source_name, agency, source_url, license,
                            rights_notes, is_broll_label, selected, meta)
                            values (%s,%s,'clip','stock_broll',%s,%s,%s,%s,'Licensed',%s,true,true,%s)""",
                         (c.pid, s["id"], cand.title, cand.source_name, cand.agency, cand.source_url, cand.rights_note,
                          db.jsonb({"media_url": cand.media_url, "duration": cand.duration, "auto_rights": True})))
                    used.add(cand.source_url)
                    nst += 1
        for n in needs:
            db.x("update footage_plan set status=%s where id=%s", ("found" if (nv + ni + nst) else "gap", n["id"]))
        db.x("delete from assets where scene_id=%s and tier in ('generated','gap') and kind='card'", (s["id"],))
        db.x("""insert into assets (project_id, scene_id, kind, tier, title, source_name, license, selected, rights_notes)
                values (%s,%s,'card','generated',%s,'YouTube Channel Builder','Licensed',true,'Made by the app for this video')""",
             (c.pid, s["id"], f"Maps, timeline, chart and document graphics, scene {s['idx']}"))
        c.progress("footage", 0.6 * (k + 1) / len(scenes), f"scene {s['idx']}: {nv} videos, {ni} photos, {nst} B-roll")
    # Picture edit: match a picture to every sentence (searches more where nothing fits).
    used_photos: set = set()
    clip_uses: dict = {}
    gen = {"off": settings.gen_media == "off" or c.fx is not None or not settings.gemini_api_key,
           "images": settings.gen_images_max, "videos": settings.gen_videos_max}
    for k, s in enumerate(c.scenes()):
        matched = plan_scene_shots(c, s, used, used_photos, clip_uses, gen)
        c.progress("footage", 0.6 + 0.4 * (k + 1) / len(scenes), f"scene {s['idx']}: {matched} shots matched to the narration")
    return None


def _yt_instructions(e: dict) -> str:
    return (f"Official video from {e.get('channel') or 'the agency'}"
            f"{' (' + e['published'] + ')' if e.get('published') else ''}. YouTube does not offer a file download and the app "
            "may not download from YouTube. To get the file: (1) look for a download link on the agency's own website or "
            "transparency portal, or (2) ask the agency's records office for a copy of the released video (a public-records "
            "request; usually free or low cost, can take days or weeks). When you have the file, upload it here and add "
            "start/end times if you only need part. Otherwise press Skip: you can add it later and re-render that scene.")


def _d(s):
    return s if s and re.match(r"\d{4}-\d{2}-\d{2}$", s) else None


def _download(c: Ctx, asset: dict) -> None:
    """Fetch an approved asset from a source that allows it. Videos are normalised to MP4 (Commons serves WebM)."""
    import requests
    from ..footage.discovery import UA
    meta = asset["meta"] or {}
    url = meta.get("media_url")
    if asset["kind"] == "image" and meta.get("thumb_url"):
        url = meta["thumb_url"]  # 1920 px rendition; originals can be huge TIFFs
    if not url and meta.get("identifier"):  # Internet Archive: resolve the item's video file
        url = sources.internet_archive_media_url(meta["identifier"])
    if not url and meta.get("nasa_manifest"):
        from ..footage.discovery import nasa_media_url
        url = nasa_media_url(meta["nasa_manifest"], "image" if asset["kind"] == "image" else "video")
    if not url:
        raise agents.BusinessRuleViolation(f"asset {asset['id']} has no downloadable file; upload it on the Footage Desk")
    image = asset["kind"] == "image"
    ext = ".jpg" if image else ".mp4"
    key = project_key(c.pid, "images" if image else "clips", "raw", f"{asset['id']}{ext}")
    if not store().exists(key) and not image and meta.get("auto_rights"):
        # Automatic picks only use the first minute: FFmpeg reads just that part straight from the source (NASA files
        # can be several GB). Never fall back to downloading the whole file for these; a failure drops the clip.
        import time as _t
        from ..media.ff import run as ff
        out = c.work / "dl" / f"{asset['id']}{ext}"
        out.parent.mkdir(parents=True, exist_ok=True)
        last = None
        for attempt in range(3):
            try:
                ff(["-rw_timeout", "60000000", "-user_agent", UA.get("User-Agent", "ycb"), "-t", "60", "-i", url, "-t", "60",
                    "-vf", "scale='min(1920,iw)':-2", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21", "-threads", "2",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(out)], timeout=900)
                store().put(out, key, "video/mp4")
                break
            except Exception as e:  # noqa: BLE001
                last = e
                out.unlink(missing_ok=True)
                if "429" in str(e) or "Too Many" in str(e):
                    _t.sleep(20 * (attempt + 1))  # Wikimedia asks bulk clients to slow down
                    continue
                break
        if not store().exists(key):
            raise agents.BusinessRuleViolation(f"could not read the first minute of the clip: {str(last)[-200:]}")
    if not store().exists(key):
        tmp = c.work / "dl" / f"{asset['id']}.src"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=180, headers=UA) as r:
            r.raise_for_status()
            size = int(r.headers.get("content-length") or 0)
            if size > 800 * 1024 * 1024:
                raise agents.BusinessRuleViolation(f"file is {size // 2**20} MB; too large to download")
            with open(tmp, "wb") as f:
                for ch in r.iter_content(1 << 20):
                    f.write(ch)
        out = c.work / "dl" / f"{asset['id']}{ext}"
        if image:
            from PIL import Image
            Image.open(tmp).convert("RGB").save(out, quality=92)
        elif url.lower().split("?")[0].endswith(".mp4") and not meta.get("auto_rights"):
            tmp.replace(out)
        else:  # normalise to H.264 MP4; automatic picks keep only the first 2 minutes (the edit never uses more)
            from ..media.ff import run as ff
            ff(["-i", str(tmp), *(["-t", "120"] if meta.get("auto_rights") else []), "-vf",
                "scale='min(1920,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", str(out)])
        store().put(out, key, "image/jpeg" if image else "video/mp4")
    db.x("update assets set storage_path=%s where id=%s", (key, asset["id"]))


def _fetch_selected(c: Ctx, stage: str = "blur", share: float = 0.6) -> None:
    """Download every approved asset that is still only a link, 4 at a time, reporting progress as it goes."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    todo = db.q("""select * from assets where project_id=%s and selected and storage_path is null
                   and kind in ('clip','image') and tier in ('real','news_document','stock_broll')""", (c.pid,))
    if not todo:
        return

    def one(a: dict):
        try:
            _download(c, a)
            return None
        except Exception as e:  # noqa: BLE001 - a dead link or bad file drops that item, not the whole video
            if not (a["meta"] or {}).get("auto_rights"):
                return e
            meta = dict(a["meta"] or {})
            meta["download_error"] = f"{type(e).__name__}: {str(e)[:200]}"
            db.x("update assets set selected=false, meta=%s where id=%s", (db.jsonb(meta), a["id"]))
            return None

    done, err = 0, None
    # Photos are quick: 6 at a time. Videos are transcoded on a small CPU: 2 at a time.
    for kind, n in (("image", 6), ("clip", 2)):
        batch = [a for a in todo if a["kind"] == kind]
        if not batch:
            continue
        with ThreadPoolExecutor(n) as pool:
            futs = [pool.submit(one, a) for a in batch]
            try:
                for f in as_completed(futs):
                    done += 1
                    err = err or f.result()
                    c.progress(stage, share * done / len(todo), f"downloading {'photos' if kind == 'image' else 'video clips'} {done}/{len(todo)}")
            except BaseException:
                for f in futs:
                    f.cancel()  # cancelled by the user: stop starting new downloads
                raise
    if err:
        raise err


# ------------------------------------------------------------------ stage: blur
def stage_blur(c: Ctx) -> None:
    _fetch_selected(c)
    from ..agents.niches import preset_for
    ch = db.q1("select niche, sub_niche from channels where id=%s", (c.project["channel_id"],)) or {}
    blur_needed = preset_for(ch.get("niche"), ch.get("sub_niche"))["key"] == "true_crime"
    scenes = c.scenes()
    for k, s in enumerate(scenes):
        clips = db.q("""select * from assets where scene_id=%s and selected and kind='clip' and storage_path is not null""", (s["id"],))
        if not clips:
            db.x("update scenes set blur_status='not_needed' where id=%s and blur_status<>'signed_off'", (s["id"],))
            continue
        for a in clips:
            if not blur_needed and (a["meta"] or {}).get("auto_rights") and a["tier"] != "news_document":
                # Licence-checked archive/stock footage in non-crime niches: no private people to protect; keep as is.
                if (a["meta"] or {}).get("blurred_key") != a["storage_path"]:
                    meta = dict(a["meta"])
                    meta["blurred_key"] = a["storage_path"]
                    db.x("update assets set meta=%s where id=%s", (db.jsonb(meta), a["id"]))
                continue
            manual = db.q("select * from redaction_plan where asset_id=%s and kind='manual_box'", (a["id"],))
            cuts = db.q("select * from redaction_plan where asset_id=%s and kind='graphic_cutaway'", (a["id"],))
            h = sha(a["storage_path"], [dict(m) for m in manual], [dict(x) for x in cuts], "blur-v1")
            out_key = project_key(c.pid, "clips", "blurred", f"{a['id']}-{h}.mp4")
            if a["meta"].get("blurred_key") == out_key and store().exists(out_key):
                continue
            src = c.local(a["storage_path"])
            an = blur.analyse(src, text_pii=a["tier"] in ("real", "news_document"),
                              on_progress=lambda f: c.progress("blur", 0.6 + 0.4 * (k + f * 0.5) / len(scenes), f"analysing scene {s['idx']}"))
            out = c.work / "blurred" / f"{a['id']}.mp4"
            blur.render(src, out, an,
                        manual=[{"t_start": float(m["t_start"]), "t_end": float(m["t_end"]), "box": m["box"]} for m in manual],
                        cutaways=[{"t_start": float(x["t_start"]), "t_end": float(x["t_end"])} for x in cuts])
            store().put(out, out_key, "video/mp4")
            akey = project_key(c.pid, "clips", "blurred", f"{a['id']}-{h}.tracks.json")
            store().put(blur.save_analysis(an, c.work / "blurred" / f"{a['id']}.json"), akey, "application/json")
            db.x("delete from redaction_plan where asset_id=%s and origin='auto'", (a["id"],))
            for t in an["tracks"]:
                db.x("""insert into redaction_plan (project_id, asset_id, scene_idx, kind, origin, track_id, t_start, t_end, reason, meta)
                        values (%s,%s,%s,%s,'auto',%s,%s,%s,%s,%s)""",
                     (c.pid, a["id"], s["idx"], "face" if t["kind"] == "face" else ("plate" if t["kind"] == "plate" else "text_pii"),
                      t["id"], t["start"], t["end"], "auto-detected", db.jsonb({"tracks_key": akey, "hits": t["hits"]})))
            meta = dict(a["meta"])
            meta["blurred_key"] = out_key
            db.x("update assets set meta=%s where id=%s", (db.jsonb(meta), a["id"]))
        db.x("update scenes set blur_status='needs_review' where id=%s", (s["id"],))
        c.progress("blur", 0.6 + 0.4 * (k + 1) / len(scenes), f"scene {s['idx']} ready")


# ------------------------------------------------------------------ stage: assemble
ANIM_SECONDS = {"route": 9.0, "place": 7.0, "counter": 5.5, "bars": 8.0, "timeline": 9.0, "points": 8.0, "quote": 7.0}
MOVES = ["in", "right", "out", "left", "up"]


def _niche(c: Ctx) -> str:
    if not hasattr(c, "_niche"):
        from ..agents.niches import preset_for
        ch = db.q1("select niche, sub_niche from channels where id=%s", (c.project["channel_id"],)) or {}
        c._niche = preset_for(ch.get("niche"), ch.get("sub_niche"))["key"]
    return c._niche


def _subject_latlon(c: Ctx):
    if not hasattr(c, "_latlon"):
        place = _case(c).get("location")
        hit = maps.geocode(place) if place else None
        c._latlon = (hit[0], hit[1], place) if hit else None
    return c._latlon


def _anchor_time(anchor: str, sents: list[dict]) -> float | None:
    """Start time of the sentence that best matches the anchor words (the animation lands as it is spoken)."""
    aw = set(re.findall(r"[a-z0-9']+", (anchor or "").lower()))
    if len(aw) < 3 or not sents:
        return None
    best, score = None, 0.0
    for x in sents:
        sw = set(re.findall(r"[a-z0-9']+", x["text"].lower()))
        sc = len(aw & sw) / len(aw)
        if sc > score:
            best, score = x, sc
    return float(best["start"]) if best and score >= 0.6 else None


def _anim_maker(kind: str, g: dict, bg_args: dict, accent):
    """A callable (out, seconds) -> Path that renders the animation; the backdrop is built at render time."""
    from ..media import anim

    def make(out: Path, secs: float) -> Path:
        bg = anim.Backdrop(**bg_args, accent=accent)
        if kind == "title":
            return anim.title_card(out, g["number"], g["title"], g.get("kicker", ""), secs, accent, bg)
        if kind == "route":
            return anim.route(out, tuple(g["a"]), tuple(g["b"]), secs, accent)
        if kind == "place":
            return anim.place(out, g["lat"], g["lon"], g["place_label"], secs, sub=g.get("title", "")[:40], accent=accent)
        if kind == "counter":
            return anim.counter(out, g["value"], g.get("unit", ""), g["title"][:60], secs, accent, bg=bg)
        if kind == "bars":
            return anim.bars(out, g.get("title", "")[:48], g["labels"], g["values"], g.get("unit", ""), secs, accent, bg=bg)
        if kind == "timeline":
            items = [(x.split(":", 1)[0].strip(), x.split(":", 1)[-1].strip()) for x in g["items"]]
            return anim.timeline(out, items, secs, accent, g.get("title", "")[:48], bg=bg)
        if kind == "points":
            return anim.points(out, g.get("title", "")[:48], g["items"], secs, accent, bg=bg)
        if kind == "quote":
            return anim.quote(out, g["text"], g.get("title", "")[:40], secs, accent, bg=bg)
        if kind == "framed":
            return anim.framed_photo(out, Path(g["photo"]), secs, "", accent, tilt=g.get("tilt", -3.0), bg=bg)
        raise ValueError(kind)
    return make


def scene_segments(c: Ctx, s: dict, n_scenes: int) -> tuple[list[compose.Segment], str]:
    """Visual track for a scene, explainer-documentary style:
    animated chapter title -> the planned animations (maps, routes, counters, bars, timelines...) placed on the
    sentence they explain -> real footage and photos (camera moves, short cuts) filling the time between them.
    Backgrounds of the animations change shot to shot (scene photo, satellite, glows, blueprint, paper...)."""
    from ..media import anim
    from ..media.cards import THUMB_ACCENTS
    dur = float(s["audio_duration_sec"])
    d = c.work / "anim" / f"scene-{s['idx']}"
    d.mkdir(parents=True, exist_ok=True)
    niche = _niche(c)
    accent = THUMB_ACCENTS.get(niche, (255, 205, 40))
    sents = list(s["sentence_timings"] or [])
    clips = db.q("""select * from assets where scene_id=%s and selected and kind='clip' and meta ? 'blurred_key'
                    order by tier='real' desc, created_at""", (s["id"],))
    photos = db.q("""select * from assets where scene_id=%s and selected and kind='image' and storage_path is not null
                     order by created_at""", (s["id"],))
    photo_paths = [c.local(a["storage_path"]) for a in photos]
    latlon = _subject_latlon(c)
    nk = [0]

    def backdrop() -> dict:
        k = nk[0]
        nk[0] += 1
        style = anim.pick_style(s["idx"], k, niche, bool(photo_paths), bool(latlon))
        return {"style": style, "niche": niche, "seed": s["idx"] * 31 + k,
                "photo": str(photo_paths[(s["idx"] + k) % len(photo_paths)]) if photo_paths else None,
                "latlon": latlon[:2] if latlon else None}

    def anim_seg(kind: str, g: dict, secs: float) -> compose.Segment:
        bg = backdrop()
        name = f"{kind}-{sha(kind, g, bg, round(secs, 2))[:10]}.mp4"
        return compose.Segment("anim", d / name, secs, make=_anim_maker(kind, g, bg, accent))

    # 1) planned animations, placed where the narration talks about them
    gs = [dict(g) for g in (s.get("graphics") or [])]
    if s["idx"] == 0 and latlon and not any(g["kind"] == "place" for g in gs):
        gs.insert(0, {"kind": "place", "lat": latlon[0], "lon": latlon[1], "anchor": "",
                      "place_label": (_case(c).get("case_name") or latlon[2]).split(",")[0][:34], "title": latlon[2][:40],
                      "_at": 5.5})
    if not any(g["kind"] == "bars" for g in gs):  # the script's sourced chart, if any, becomes animated bars
        pack = c.project["production_pack"] or {}
        ch = next((x.get("chart") for x in (pack.get("script") or {}).get("scenes", []) if x.get("idx") == s["idx"]), None)
        if ch:
            gs.append({"kind": "bars", "title": ch["title"], "labels": ch["labels"], "values": [float(v) for v in ch["values"]],
                       "unit": ch.get("unit", ""), "anchor": ""})
    TITLE = min(5.0, dur)
    events: list[tuple[float, dict]] = []
    unplaced = []
    for g in gs:
        t = g.get("_at") or _anchor_time(g.get("anchor", ""), sents)
        (events.append((t, g)) if t is not None else unplaced.append(g))
    # graphics without a matching sentence are spread through the free part of the scene
    for i, g in enumerate(unplaced):
        events.append((TITLE + (dur - TITLE) * (i + 1) / (len(unplaced) + 1), g))
    events.sort(key=lambda e: e[0])
    placed, cursor = [], TITLE + 2.0
    for t, g in events:
        secs = ANIM_SECONDS.get(g["kind"], 7.0)
        t = max(t, cursor)
        if t + secs > dur - 1.0:
            continue
        placed.append((t, secs, g))
        cursor = t + secs + 4.0  # at least a few seconds of footage between animations

    # 2) footage: the shot list (Shot List Agent) says which picture shows each beat of narration; anything not
    #    covered by it is filled from the scene's other media, least-used first. Clips are cut into short shots at
    #    moving in-points; photos get camera moves; shots carry their factual on-screen label.
    media: dict[str, dict] = {}
    for a in clips:
        p = c.local(a["meta"]["blurred_key"])
        src = duration(p)
        a_in, a_out = float(a["in_sec"] or 0), min(src, float(a["out_sec"] or src))
        if a_out - a_in < 3:  # in/out points beyond the part that was downloaded: use the whole downloaded piece
            a_in, a_out = 0.0, src
        media[str(a["id"])] = {"kind": "clip", "path": p, "in": a_in, "out": max(a_in + 1.0, a_out), "pos": a_in,
                               "cap": 12.0 if a["tier"] == "real" else 8.0, "uses": 0, "ai": a["tier"] == "generated"}
    for i, (a, p) in enumerate(zip(photos, photo_paths)):
        media[str(a["id"])] = {"kind": "photo", "path": p, "n": i, "uses": 0, "ai": a["tier"] == "generated"}
    shots = [x for x in (s.get("shots") or []) if s.get("shots_hash") == s["narration_hash"]]
    planned_ids = {x["asset_id"] for x in shots if x.get("asset_id")}
    pool = [m for k, m in media.items() if k not in planned_ids] or list(media.values())
    labelled: set[int] = set()
    uses = [0]
    fallback_sents = [x for x in sents if 8 <= len(x["text"].split()) <= 30]

    def shot_at(t: float):
        cur = None
        for k, x in enumerate(shots):
            if x["start"] - 0.3 <= t:
                cur = k
            else:
                break
        return cur

    def pool_pick():
        if not pool:
            return None
        m = min(pool, key=lambda m: (m["uses"], m.get("n", 0)))
        return m

    def media_seg(m: dict, take: float, label: str) -> compose.Segment:
        if m.get("ai") and "llustration" not in label:
            label = "AI illustration"  # generated scenes are always marked on screen
        m["uses"] += 1
        uses[0] += 1
        if m["kind"] == "clip":
            take = min(take, m["cap"])
            if m["pos"] + take > m["out"]:
                m["pos"] = m["in"]  # wrap around inside the clip
            seg = compose.Segment("clip", m["path"], take, in_sec=m["pos"], label=label)
            m["pos"] += take
            return seg
        take = min(take, 9.0)
        if not label and not m.get("ai") and uses[0] % 6 == 4 and take >= 5:  # now and then: a framed print
            return anim_seg("framed", {"photo": str(m["path"]), "tilt": -3.0 if m["n"] % 2 else 2.5}, take)
        return compose.Segment("image", m["path"], take, pan=MOVES[(m["n"] + m["uses"]) % len(MOVES)], photo=True, label=label)

    def fill(t0: float, t1: float, segs: list) -> None:
        t = t0
        while t1 - t > 0.05:
            left = t1 - t
            if left < 2.5 and segs:
                segs[-1].duration += left
                return
            k = shot_at(t)
            sh = shots[k] if k is not None else None
            m = media.get(sh["asset_id"]) if sh and sh.get("asset_id") else None
            if m is not None and m["kind"] == "photo" and m["uses"] > 0 and sh and t > sh["start"] + 9:
                m = None  # a long beat: after one photo, continue with another picture
            if m is not None:
                end = shots[k + 1]["start"] if k + 1 < len(shots) else t1
                take = max(2.5, min(left, end - t))
            else:
                m = pool_pick()
                take = min(7.0, left)
            label = ""
            if sh and k not in labelled and sh.get("label") and m is not None:
                label, _ = sh["label"], labelled.add(k)
            if m is not None:
                seg = media_seg(m, take, label)
                segs.append(seg)
                t += seg.duration
                continue
            # no footage at all for this scene: the key line being spoken, animated over a changing backdrop
            x = next((x for x in fallback_sents if float(x["start"]) >= t - 1), None) or \
                (fallback_sents[uses[0] % len(fallback_sents)] if fallback_sents else None)
            uses[0] += 1
            take = min(8.0, left)
            if x:
                segs.append(anim_seg("quote", {"text": x["text"], "title": s["chapter_title"] or ""}, take))
            else:
                segs.append(anim_seg("title", {"number": s["idx"] + 1, "title": s["chapter_title"] or s["title"]}, take))
            t += take

    segs: list[compose.Segment] = []
    segs.append(anim_seg("title", {"number": s["idx"] + 1, "title": s["chapter_title"] or s["title"],
                                   "kicker": (c.project["selected_title"] or c.project["title"])[:70]}, TITLE))
    t = TITLE
    for (t_start, secs, g) in placed:
        fill(t, t_start, segs)
        segs.append(anim_seg(g["kind"], g, secs))
        t = t_start + secs
    fill(t, dur, segs)
    total = sum(x.duration for x in segs)
    segs[-1].duration += dur - total  # exact length
    if segs[-1].duration < 0.5 and len(segs) > 1:
        segs[-2].duration += segs[-1].duration
        segs.pop()
    h = sha(CARD_STYLE_VERSION, s["narration_hash"], dur, [(x.kind, x.path.name, round(x.duration, 2), round(x.in_sec, 2), x.pan,
                                                            x.label) for x in segs])
    return segs, h


def _case(c: Ctx) -> dict:
    if not c.project["idea_id"]:
        return {}
    r = db.q1("select case_info from ideas where id=%s", (c.project["idea_id"],))
    return (r or {}).get("case_info") or {}


def stage_assemble(c: Ctx) -> None:
    scenes = c.scenes()
    spoken = sum(float(s["audio_duration_sec"] or 0) for s in scenes)
    if spoken < 0.4 * 60 * float(c.project["target_minutes"]) and not c.fx:
        raise agents.BusinessRuleViolation(
            f"the narration audio is only {spoken:.0f} s for a {c.project['target_minutes']}-minute video; "
            "re-render so the script and voice are made again")
    missing = [s for s in scenes if s.get("graphics") is None and s["narration"].strip()]
    if missing:  # projects scripted before motion graphics existed get them now
        claims = db.q("select * from research_claims where project_id=%s order by claim", (c.pid,))
        for s in missing:
            sc = [cl["claim"] for cl in claims if cl["scene_idx"] in (None, s["idx"])]
            g = agents.plan_graphics(c.project, s, sc, c.prov, c.model, c.fx, c.uid, c.rid)
            db.x("update scenes set graphics=%s where id=%s", (db.jsonb(g), s["id"]))
            s["graphics"] = g
    parts, wavs = [], []
    for k, s in enumerate(scenes):
        key = project_key(c.pid, "clips", f"scene-{s['idx']}.mp4")
        segs, h = scene_segments(c, s, len(scenes))
        if s["visual_hash"] == h and store().exists(key):
            parts.append(c.local(key) if not (c.work / "scenes" / f"scene-{s['idx']}-{h}.mp4").exists()
                         else c.work / "scenes" / f"scene-{s['idx']}-{h}.mp4")
        else:
            out = c.work / "scenes" / f"scene-{s['idx']}-{h}.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            seg_dir = c.work / "segs" / f"scene-{s['idx']}"
            seg_dir.mkdir(parents=True, exist_ok=True)
            compose.scene_video(segs, seg_dir, out)
            store().put(out, key, "video/mp4")
            shutil.rmtree(seg_dir, ignore_errors=True)  # per-segment scratch files are no longer needed
            db.x("update scenes set visual_path=%s, visual_hash=%s, footage_type=%s where id=%s",
                 (key, h, _footage_type(s), s["id"]))
            parts.append(out)
        wavs.append(c.local(s["audio_path"]))
        c.progress("assemble", (k + 1) / len(scenes) * 0.6, f"scene {s['idx']} composed")

    # End card: last 20 s with "thanks for watching", a subscribe circle and two "watch next" frames that the
    # YouTube end-screen elements are placed on (YouTube allows end screens only in the last 5-20 s).
    if not c.fx:
        outro, silence = end_card_part(c)
        parts.append(outro)
        wavs.append(silence)
    vid = compose.concat_videos(parts, c.work / "program.mp4")
    mutes = [(float(r["t_start"]), float(r["t_end"])) for r in
             db.q("select * from redaction_plan where project_id=%s and kind in ('audio_mute','audio_bleep') and asset_id is null and t_end>t_start", (c.pid,))]
    narration = compose.concat_audio(wavs, c.work / "narration.wav", mutes)
    music = _music(c, duration(narration))
    audio, stats = compose.mix(narration, music, c.work / "mix.m4a")
    c.progress("assemble", 0.75, "mixed and loudness-normalised")

    # captions with real sentence timings (absolute)
    sents = []
    for s in c.scenes():
        for x in s["sentence_timings"] or []:
            sents.append({"text": x["text"], "start": float(s["start_sec"]) + x["start"], "end": float(s["start_sec"]) + x["end"]})
    omit = [r["reason"] for r in db.q("select reason from redaction_plan where project_id=%s and kind='caption_omit'", (c.pid,)) if r["reason"]]
    srt = c.work / "captions.srt"
    srt.write_text(captions.build_srt(sents, omit), encoding="utf-8")

    chapters = chapter_list(c)
    meta = c.work / "chapters.ffmeta"
    total = duration(narration)
    meta.write_text(compose.ffmetadata([(a, (chapters[i + 1][0] if i + 1 < len(chapters) else total), t)
                                        for i, (a, t) in enumerate(chapters)], c.project["selected_title"] or c.project["title"]))
    mode = c.project["caption_mode"]
    out = c.work / f"v-{c.render['version']}.mp4"
    boxes = [{"t_start": float(r["t_start"]), "t_end": float(r["t_end"]), **r["box"]} for r in
             db.q("select * from redaction_plan where project_id=%s and kind='manual_box' and asset_id is null and box is not null", (c.pid,))]
    compose.final_mux(vid, audio, srt, meta, out, burn=mode in ("burned", "both"), sidecar=mode in ("sidecar", "both"),
                      manual_boxes=boxes)
    c.progress("assemble", 0.95, "final mux done")

    check = acceptance(out)
    if not check["has_audio"] or check.get("silence_ratio", 1) >= 0.2:
        raise RuntimeError(f"export failed acceptance (silent or missing audio): {check}")
    rk = project_key(c.pid, "renders", f"v-{c.render['version']}.mp4")
    store().put(out, rk, "video/mp4")
    store().put(srt, project_key(c.pid, "captions.srt"), "application/x-subrip")
    vtt = c.work / "captions.vtt"  # same cues for the in-browser player
    vtt.write_text("WEBVTT\n\n" + re.sub(r"(\d\d:\d\d:\d\d),(\d\d\d)", r"\1.\2", srt.read_text(encoding="utf-8")), encoding="utf-8")
    store().put(vtt, project_key(c.pid, "captions.vtt"), "text/vtt")
    db.x("""update renders set mp4_path=%s, srt_path=%s, duration_sec=%s, has_audio=%s, integrated_lufs=%s, ffprobe=%s where id=%s""",
         (rk, project_key(c.pid, "captions.srt"), check["duration"], check["has_audio"], check.get("integrated_lufs"),
          db.jsonb({**check, "loudnorm_first_pass": stats}), c.rid))
    c.progress("assemble", 1.0, "export verified with ffprobe")


def end_card_part(c: Ctx) -> tuple[Path, Path]:
    from ..media import anim
    from ..media.cards import THUMB_ACCENTS
    from ..media.ff import run as ff
    niche = _niche(c)
    kit = db.q1("select selected from channel_kits where channel_id=%s order by version desc limit 1", (c.project["channel_id"],))
    channel = (((kit or {}).get("selected") or {}).get("name") or "").strip() or "this channel"
    ch = db.q1("select niche from channels where id=%s", (c.project["channel_id"],)) or {}
    topic = {"architecture": "engineering marvels", "true_crime": "true stories", "geopolitics": "world affairs explained",
             "motorsport": "racing stories", "science_space": "space and science", "business_tech": "business stories"
             }.get(niche, (ch.get("niche") or "documentaries").lower()[:40])
    photo = db.q1("""select storage_path from assets where project_id=%s and selected and kind='image' and tier='real'
                     and storage_path is not null order by created_at limit 1""", (c.pid,))
    bg = anim.Backdrop("photo" if photo else "aurora", THUMB_ACCENTS.get(niche, (255, 205, 40)), niche=niche,
                       photo=c.local(photo["storage_path"]) if photo else None, seed=11)
    out = c.work / "scenes" / "end-card.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    anim.end_card(out, channel, topic, anim.END_CARD_SECONDS, THUMB_ACCENTS.get(niche, (255, 205, 40)), bg)
    silence = c.work / "end-silence.wav"
    ff(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{anim.END_CARD_SECONDS:.2f}", "-c:a", "pcm_s16le", str(silence)])
    return out, silence


def _footage_type(s: dict) -> str:
    r = db.q1("""select tier from assets where scene_id=%s and selected and kind='clip'
                 order by case tier when 'real' then 1 when 'news_document' then 2 when 'stock_broll' then 3 else 4 end limit 1""", (s["id"],))
    return r["tier"] if r else "generated"


def _music(c: Ctx, seconds: float) -> Path:
    m = db.q1("select * from assets where project_id=%s and kind='music' and selected and storage_path is not null", (c.pid,))
    if m:
        return c.local(m["storage_path"])
    bed = c.work / "ambient_bed.wav"
    if not bed.exists() or abs(duration(bed) - seconds) > 1:
        compose.ambient_bed(seconds, bed)
    return bed


def chapter_list(c: Ctx) -> list[tuple[float, str]]:
    out = []
    for s in c.scenes():
        out.append((0.0 if s["idx"] == 0 else float(s["start_sec"]), s["chapter_title"] or s["title"]))
    return out


# ------------------------------------------------------------------ stage: package (Editor + SEO)
def apply_chapters(description: str, chapters: list[tuple[float, str]]) -> str:
    block = "\n".join(f"{agents.fmt_ts(t)} {title}" for t, title in chapters)
    lines = description.splitlines()
    idx = [i for i, l in enumerate(lines) if re.match(r"^\d{1,2}:\d{2}(:\d{2})?\s", l)]
    if idx:
        lines = lines[:idx[0]] + [block] + [l for i, l in enumerate(lines) if i > idx[0] and i not in idx]
        return "\n".join(lines)
    return description.replace("SOURCES_AND_CREDITS", block + "\n\nSOURCES_AND_CREDITS")


def _voice_and_music(c: Ctx) -> list[str]:
    out = []
    m = db.q1("select * from assets where project_id=%s and kind='music' and selected", (c.pid,))
    out.append(f"- Music: {m['title']} — {m['source_name']} ({m['license']})" if m else
               "- Music: ambient score generated by the app (no third-party music)")
    v = pricing.P["tts"].get(f"{c.project['voice_provider']}:{c.project['voice_id']}") or \
        next((x for k, x in pricing.P["tts"].items() if k.startswith(c.project["voice_provider"] + ":")), {})
    out.append(f"- Narration voice: {v.get('label', c.project['voice_provider'])} (AI-generated)")
    return out


def credits(c: Ctx) -> str:
    """Full per-item credits (every source, clip and photo): stored with the render and shown on the public
    credits page; also written into the upload pack."""
    rows = db.q("""select * from assets where project_id=%s and selected and tier not in ('gap','generated')
                   order by tier, created_at""", (c.pid,))
    out = ["Sources and footage credits:"]
    for s in db.q("select * from research_sources where project_id=%s order by length(ref), ref", (c.pid,)):
        out.append(f"- [{s['ref']}] {s['title']} — {s['publisher'] or ''} {s['url'] or ''}".rstrip())
    seen = set()
    for a in rows:
        line = "- " + rights.edit_note(a)
        if line not in seen:
            seen.add(line)
            out.append(line)
    if db.q1("select 1 from assets where project_id=%s and selected and tier='generated' and (meta->>'ai')::boolean", (c.pid,)):
        out.append("- AI illustrations (scale-model scenes, marked on screen): generated for this video with Google Gemini")
    out.append("- Maps, charts and animations: made for this video (satellite imagery NASA Blue Marble/GIBS and USGS, "
               "public domain; map data Natural Earth, public domain; geocoding © OpenStreetMap contributors, ODbL)")
    out += _voice_and_music(c)
    return "\n".join(out)


CREDITS_PAGE = "https://youtube-channel-builder.vercel.app/credits/"


def credits_short(c: Ctx, budget: int = 1600) -> str:
    """Credits for the YouTube description: grouped by source and licence, authors named for CC BY items (the
    licence requires it), and a link to the full per-item list. Kept under `budget` characters."""
    rows = db.q("""select source_name, license, agency, meta, kind from assets where project_id=%s and selected
                   and tier in ('real','news_document','stock_broll')""", (c.pid,))
    groups: dict[tuple[str, str], dict] = {}
    for a in rows:
        lic = (a["meta"] or {}).get("license_short") or a["license"] or "licence on file"
        g = groups.setdefault((a["source_name"] or "Other", lic), {"n": 0, "authors": []})
        g["n"] += 1
        au = (a["agency"] or "").strip()
        if "BY" in lic.upper() and au and au not in g["authors"] and au.lower() not in ("unknown author", "unknown"):
            g["authors"].append(au)
    # The sources the script actually cites most (claims carry their source refs), never the whole search result list.
    srcs = db.q("""select s.title, s.publisher from research_sources s
                   join (select r, count(*) n from research_claims c, unnest(c.source_refs) r where c.project_id=%s group by r) k
                     on k.r = s.ref
                   where s.project_id=%s and coalesce(s.excerpt, '') <> ''
                   order by k.n desc, s.ref limit 6""", (c.pid, c.pid))
    ordered = sorted(groups.items(), key=lambda kv: -kv[1]["n"])
    ai_used = bool(db.q1("select 1 from assets where project_id=%s and selected and tier='generated' and (meta->>'ai')::boolean",
                         (c.pid,)))

    def build(n_src: int, n_auth: int, n_groups: int) -> str:
        lines = ["Sources:"] + [f"- {x['title']}" + (f" ({x['publisher']})" if x["publisher"] else "") for x in srcs[:n_src]]
        lines.append("Footage and photos:")
        for (src, lic), g in ordered[:n_groups]:
            line = f"- {src}, {lic}: {g['n']} item{'s' if g['n'] != 1 else ''}"
            if g["authors"]:
                extra = len(g["authors"]) - n_auth
                line += " by " + ", ".join(g["authors"][:n_auth]) + (f" and {extra} more" if extra > 0 else "")
            lines.append(line)
        if len(ordered) > n_groups:
            lines.append(f"- {sum(g['n'] for _, g in ordered[n_groups:])} more items from other free sources")
        if ai_used:
            lines.append("- AI illustrations (marked on screen) generated for this video with Google Gemini")
        lines.append("- Maps, charts and animations made for this video (NASA/USGS imagery and Natural Earth data, public domain)")
        lines += _voice_and_music(c)
        lines.append(f"Full credits for every clip and photo: {CREDITS_PAGE}{c.rid}")
        return "\n".join(lines)

    # shrink step by step until it fits: fewer sources, fewer named authors, fewer groups (all stay on the page)
    for n_src, n_auth, n_groups in ((6, 12, 12), (4, 8, 10), (3, 5, 8), (2, 3, 6), (1, 2, 4), (0, 1, 3), (0, 0, 2)):
        text = build(n_src, n_auth, n_groups)
        if len(text) <= budget:
            return text
    return text


def fit_description(desc: str, limit: int = 4600) -> str:
    """YouTube allows 5,000 characters and no angle brackets; leave room for the 'watch next' links the app adds."""
    desc = desc.replace("<", "(").replace(">", ")")
    if len(desc) <= limit:
        return desc
    parts = desc.split("\n\n")
    while len("\n\n".join(parts)) > limit and len(parts) > 2:
        longest = max(range(1, len(parts) - 1), key=lambda i: len(parts[i]) if not parts[i].startswith("0:00") else -1)
        lines = parts[longest].splitlines()
        if len(lines) > 2:
            parts[longest] = "\n".join(lines[:-1])
        else:
            parts.pop(longest)
    return "\n\n".join(parts)[:limit]


def make_thumbnail(c: Ctx, text: str, badge: str) -> Path:
    """Photo-led thumbnail from the video's own licence-checked footage: the best large photo of the subject.
    True-crime videos never put a photo on the thumbnail (it could show a victim or a private person)."""
    from ..agents.niches import preset_for
    from ..footage.discovery import is_graphic
    ch = db.q1("select niche, sub_niche from channels where id=%s", (c.project["channel_id"],)) or {}
    pre = preset_for(ch.get("niche"), ch.get("sub_niche"))["key"]
    accent = cards.THUMB_ACCENTS.get(pre, cards.THUMB_ACCENTS["generic"])
    photo = None
    if pre != "true_crime":
        subject = (_case(c).get("case_name") or "").lower()
        rows = db.q("""select storage_path, title from assets where project_id=%s and selected and kind='image'
                       and tier='real' and storage_path is not null order by created_at""", (c.pid,))
        best, best_score = None, -1.0
        for r in rows:
            if is_graphic(r["title"] or ""):
                continue
            try:
                p = c.local(r["storage_path"])
                from PIL import Image as _I
                w, h = _I.open(p).size
            except Exception:  # noqa: BLE001
                continue
            # big, wide photos of the subject itself win (thumbnails are 16:9)
            score = min(w, h) / 100 + (6 if w / h >= 1.15 else 0) + \
                (5 if subject and all(t in (r["title"] or "").lower() for t in subject.split()[:3]) else 0)
            if score > best_score:
                best, best_score = p, score
        photo = best
    kickers = {"true_crime": "THE FULL STORY", "architecture": "FULL DOCUMENTARY", "geopolitics": "EXPLAINED",
               "motorsport": "THE FULL STORY", "science_space": "FULL DOCUMENTARY", "business_tech": "THE FULL STORY"}
    style = "band" if c.render["version"] % 2 == 0 else "label"
    return cards.thumbnail_doc(c.work / f"thumb-v-{c.render['version']}.jpg", text, badge, photo,
                               kickers.get(pre, "FULL DOCUMENTARY"), accent, style)


def stage_package(c: Ctx) -> None:
    scenes = c.scenes()
    narration = "\n\n".join(s["narration"] for s in scenes)
    chapters = chapter_list(c)
    seo = agents.seo_pack(c.project, {"user_id": c.uid}, c.prov, c.model, c.fx, narration,
                          [{"t": agents.fmt_ts(t), "title": n} for t, n in chapters], idem=f"render:{c.rid}:seo")
    full_credits = credits(c)
    db.x("update renders set credits_full=%s where id=%s", (full_credits, c.rid))
    desc = apply_chapters(seo.description, chapters).replace("SOURCES_AND_CREDITS", credits_short(c))
    if seo.disclosure_line not in desc:
        desc += "\n\n" + seo.disclosure_line
    desc = fit_description(desc)
    title = c.project["selected_title"] if c.project["selected_title"] in seo.titles else seo.titles[0]
    db.x("update projects set description=%s, tags=%s, disclosure_line=%s, selected_title=coalesce(selected_title,%s) where id=%s",
         (desc, seo.tags, seo.disclosure_line, title, c.pid))
    c.project = db.q1("select * from projects where id=%s", (c.pid,))

    # script.txt
    script = [f"{c.project['selected_title']}\n"]
    for s in scenes:
        script.append(f"\n[{agents.fmt_ts(float(s['start_sec']))}] {s['chapter_title'] or s['title']}\n\n{s['narration']}\n")
    sp = c.work / "script.txt"
    sp.write_text("".join(script), encoding="utf-8")
    # upload pack
    pack = c.work / "youtube-upload-pack.txt"
    pack.write_text("\n".join([
        "TITLE OPTIONS (all under 60 characters):", *[f"- {t} ({len(t)})" for t in seo.titles], "",
        f"SELECTED TITLE: {c.project['selected_title']}", "", "DESCRIPTION:", desc, "",
        "TAGS:", ", ".join(seo.tags), "", "CHAPTERS:", *[f"{agents.fmt_ts(t)} {n}" for t, n in chapters], "",
        "THUMBNAIL CONCEPT:", seo.thumbnail_concept, "THUMBNAIL PROMPT:", seo.thumbnail_prompt, "",
        "DISCLOSURE:", seo.disclosure_line, "",
        "YOUTUBE ALTERED/SYNTHETIC CONTENT SETTING: answer per YouTube's current disclosure rules (AI narration).",
        "", full_credits]), encoding="utf-8")
    kit = db.q1("select selected from channel_kits where channel_id=%s order by version desc limit 1", (c.project["channel_id"],))
    sub = ((kit or {}).get("selected") or {}).get("name") or ""
    thumb = make_thumbnail(c, seo.thumbnail_text, sub[:40])
    v = c.render["version"]
    keys = {
        "script_path": store().put(sp, project_key(c.pid, "renders", f"script-v-{v}.txt"), "text/plain"),
        "pack_path": store().put(pack, project_key(c.pid, "renders", f"upload-pack-v-{v}.txt"), "text/plain"),
        "thumb_path": store().put(thumb, project_key(c.pid, "renders", f"thumb-v-{v}.jpg"), "image/jpeg"),
    }
    db.x("update assets set selected=false where project_id=%s and kind='thumbnail'", (c.pid,))
    db.x("""insert into assets (project_id, kind, tier, storage_path, title, source_name, license, selected, graphic, meta)
            values (%s,'thumbnail','generated',%s,%s,'YouTube Channel Builder','Licensed',true,false,%s)""",
         (c.pid, keys["thumb_path"], seo.thumbnail_text, db.jsonb({"from_graphic_frame": False, "concept": seo.thumbnail_concept})))
    # compute cost for the render (wall time of the render job chain)
    started = db.q1("select min(started_at) s from jobs where render_id=%s", (c.rid,))["s"]
    secs = int(time.time() - started.timestamp()) if started else 0
    comp = pricing.P["compute"][pricing.P["tiers"][c.project["tier"]]["compute"]]
    ledger.record(user_id=c.uid, project_id=c.pid, render_id=c.rid, stage="render", agent="editor", provider="compute",
                  model=comp["label"], unit_type="seconds", units_in=secs, cost_usd=secs / 3600 * comp["per_render_hour"],
                  idempotency_key=f"compute:{c.rid}")
    db.x("""update renders set script_path=%s, pack_path=%s, thumb_path=%s, status='succeeded', stage='done', progress=100, error=null,
            finished_at=now() where id=%s""", (keys["script_path"], keys["pack_path"], keys["thumb_path"], c.rid))
    db.x("update projects set status='review' where id=%s", (c.pid,))
    db.x("update ideas set status='ready' where id=%s", (c.project["idea_id"],))
    shutil.rmtree(c.work, ignore_errors=True)  # everything is in storage now; free the work disk


HANDLERS = {"script": stage_script, "voice": stage_voice, "footage": stage_footage, "blur": stage_blur,
            "assemble": stage_assemble, "package": stage_package}


def free_work_disk(keep_project: str, min_free_gb: float = 3.0) -> list[str]:
    """If the work disk is low, delete leftover work folders of projects that have no render in progress.
    Work folders are only caches: every scene, clip, audio file and render is already in storage."""
    import os
    root = settings.work_dir
    if not root.exists():
        return []
    st = os.statvfs(root)
    if st.f_bavail * st.f_frsize / 1e9 >= min_free_gb:
        return []
    active = {str(r["project_id"]) for r in db.q("select distinct project_id from renders where status in ('queued','running','waiting_user')")}
    removed = []
    for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime):
        if d.is_dir() and d.name != keep_project and d.name not in active:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
    if os.statvfs(root).f_bavail * os.statvfs(root).f_frsize / 1e9 < min_free_gb:
        # still low: clear re-downloadable caches of the current project too (never its finished outputs in storage)
        for sub in ("cache", "segs", "dl", "blurred"):
            shutil.rmtree(root / keep_project / sub, ignore_errors=True)
    return removed


def run_stage(job: dict, hb: queue.Heartbeat) -> None:
    try:
        rp = db.q1("select project_id from renders where id=%s", (job["render_id"],))
        free_work_disk(str(rp["project_id"]) if rp else "")
    except Exception:  # noqa: BLE001 - cleanup must never block a render
        pass
    c = Ctx(job, hb)
    stage = job["stage"]
    db.x("update renders set status='running', stage=%s where id=%s and status <> 'cancelled'", (stage, c.rid))
    if hasattr(hb, "check"):
        hb.check(force=True)
    res = HANDLERS[stage](c)
    if hasattr(hb, "check"):
        hb.check(force=True)  # cancelled during this stage: do not start the next one
    if res == "wait":
        db.x("update renders set status='waiting_user', stage='footage_desk' where id=%s", (c.rid,))
        return  # the user continues from the Footage Desk, which enqueues the next stage
    nxt = STAGES.index(stage) + 1
    if nxt < len(STAGES):
        queue.enqueue(user_id=c.uid, kind="render_stage", stage=STAGES[nxt], project_id=c.pid, render_id=c.rid,
                      idempotency_key=f"render:{c.rid}:{STAGES[nxt]}", priority=50)


def start_render(project_id: str) -> dict:
    """Create a render version and enqueue its first stage (the web app calls the same SQL)."""
    p = db.q1("select * from projects where id=%s", (project_id,))
    v = db.q1("select coalesce(max(version),0)+1 v from renders where project_id=%s", (project_id,))["v"]
    est = pricing.estimate("whole_video", p["tier"], p["target_minutes"])
    r = db.q1("insert into renders (project_id, version, estimate_usd) values (%s,%s,%s) returning *", (project_id, v, est["total_usd"]))
    db.x("update projects set status='rendering' where id=%s", (project_id,))
    queue.enqueue(user_id=str(p["user_id"]), kind="render_stage", stage="script", project_id=project_id, render_id=str(r["id"]),
                  idempotency_key=f"render:{r['id']}:script", priority=50)
    return r
