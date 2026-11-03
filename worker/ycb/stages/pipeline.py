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
CARD_STYLE_VERSION = "cards-v5"


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
        done = sum(WEIGHT[s] for s in STAGES[:STAGES.index(stage)])
        total = done + WEIGHT[stage] * max(0.0, min(1.0, frac))
        db.x("update renders set stage=%s, progress=%s, status='running' where id=%s", (stage, round(total, 2), self.rid))
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
    words = sum(len(s["narration"].split()) for s in c.scenes())
    if words < 0.85 * total_words:
        raise agents.BusinessRuleViolation(f"narration is {words} words; {c.project['target_minutes']} min needs ~{total_words}")


# ------------------------------------------------------------------ stage: voice (Voice Agent)
def stage_voice(c: Ctx) -> None:
    p = c.project
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


def _yt_instructions(e: dict) -> str:
    return (f"Official video from {e.get('channel') or 'the agency'}"
            f"{' (' + e['published'] + ')' if e.get('published') else ''}. The app is not allowed to download from YouTube. "
            "Open the link, save the video with YouTube's own tools or from the agency's own website (agencies often post "
            "the same file on their transparency page), then upload it here. Before you upload, check the agency's "
            "release terms. If you only need part of it, write the start and end times in the upload form.")


def _d(s):
    return s if s and re.match(r"\d{4}-\d{2}-\d{2}$", s) else None


def _download(c: Ctx, asset: dict) -> None:
    """Fetch an approved asset from a source that allows it. Videos are normalised to MP4 (Commons serves WebM)."""
    import requests
    from ..footage.discovery import UA
    meta = asset["meta"] or {}
    url = meta.get("media_url")
    if not url and meta.get("identifier"):  # Internet Archive: resolve the item's video file
        url = sources.internet_archive_media_url(meta["identifier"])
    if not url:
        raise agents.BusinessRuleViolation(f"asset {asset['id']} has no downloadable file; upload it on the Footage Desk")
    image = asset["kind"] == "image"
    ext = ".jpg" if image else ".mp4"
    key = project_key(c.pid, "images" if image else "clips", "raw", f"{asset['id']}{ext}")
    if not store().exists(key):
        tmp = c.work / "dl" / f"{asset['id']}.src"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=180, headers=UA) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for ch in r.iter_content(1 << 20):
                    f.write(ch)
        out = c.work / "dl" / f"{asset['id']}{ext}"
        if image:
            from PIL import Image
            Image.open(tmp).convert("RGB").save(out, quality=92)
        elif url.lower().split("?")[0].endswith(".mp4"):
            tmp.replace(out)
        else:
            from ..media.ff import run as ff
            ff(["-i", str(tmp), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", str(out)])
        store().put(out, key, "image/jpeg" if image else "video/mp4")
    db.x("update assets set storage_path=%s where id=%s", (key, asset["id"]))


def _fetch_selected(c: Ctx) -> None:
    """Download every approved asset that is still only a link (approved on the Footage Desk)."""
    for a in db.q("""select * from assets where project_id=%s and selected and storage_path is null
                     and kind in ('clip','image') and tier in ('real','news_document','stock_broll')""", (c.pid,)):
        _download(c, a)


# ------------------------------------------------------------------ stage: blur
def stage_blur(c: Ctx) -> None:
    _fetch_selected(c)
    scenes = c.scenes()
    for k, s in enumerate(scenes):
        clips = db.q("""select * from assets where scene_id=%s and selected and kind='clip' and storage_path is not null""", (s["id"],))
        if not clips:
            db.x("update scenes set blur_status='not_needed' where id=%s and blur_status<>'signed_off'", (s["id"],))
            continue
        for a in clips:
            manual = db.q("select * from redaction_plan where asset_id=%s and kind='manual_box'", (a["id"],))
            cuts = db.q("select * from redaction_plan where asset_id=%s and kind='graphic_cutaway'", (a["id"],))
            h = sha(a["storage_path"], [dict(m) for m in manual], [dict(x) for x in cuts], "blur-v1")
            out_key = project_key(c.pid, "clips", "blurred", f"{a['id']}-{h}.mp4")
            if a["meta"].get("blurred_key") == out_key and store().exists(out_key):
                continue
            src = c.local(a["storage_path"])
            an = blur.analyse(src, text_pii=a["tier"] in ("real", "news_document"),
                              on_progress=lambda f: c.progress("blur", (k + f * 0.5) / len(scenes), f"analysing scene {s['idx']}"))
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
        c.progress("blur", (k + 1) / len(scenes), f"scene {s['idx']} blurred")


# ------------------------------------------------------------------ stage: assemble
def scene_segments(c: Ctx, s: dict, n_scenes: int) -> tuple[list[compose.Segment], str]:
    """Visual track for a scene: chapter card, then selected (blurred) clips, then document/timeline/statement cards."""
    dur = float(s["audio_duration_sec"])
    d = c.work / "cards" / f"scene-{s['idx']}"
    pack = c.project["production_pack"] or {}
    timeline = (pack.get("research") or {}).get("timeline") or []
    clips = db.q("""select * from assets where scene_id=%s and selected and kind='clip' and meta ? 'blurred_key'
                    order by tier='real' desc, created_at""", (s["id"],))
    photos = db.q("""select * from assets where scene_id=%s and selected and kind='image' and storage_path is not null
                     order by created_at""", (s["id"],))
    segs: list[compose.Segment] = []
    first = min(6.0, dur)
    segs.append(compose.Segment("image", cards.chapter_card(d / "chapter.png", s["idx"] + 1, s["chapter_title"] or s["title"],
                                                               c.project["selected_title"] or c.project["title"], seed=s["idx"]), first))
    remaining = dur - first
    # Location map early in the video (scene 0), when the case has a place.
    place = _case(c).get("location")
    map_key = None
    if s["idx"] == 0 and place and remaining > 10:
        mp = maps.location_map(d / "map.png", place, kicker="WHERE IT HAPPENED", seed=s["idx"])
        if mp:
            take = min(9.0, remaining - 2)
            segs.append(compose.Segment("image", mp, take, pan="right"))
            remaining -= take
            map_key = place
    # Real footage first: up to ~70% of the scene, each clip up to 60 s, honouring the in/out points set on the desk.
    budget = remaining * 0.7 if photos or s["on_screen_text"] else remaining
    for a in clips:
        if remaining <= 2 or budget <= 2:
            break
        p = c.local(a["meta"]["blurred_key"])
        src_len = duration(p)
        a_in = float(a["in_sec"] or 0)
        a_out = float(a["out_sec"] or src_len)
        avail = max(1.0, min(src_len, a_out) - a_in)
        cap = 60.0 if a["tier"] == "real" else 20.0
        take = min(remaining, budget, max(4.0, min(cap, avail)))
        segs.append(compose.Segment("clip", p, take, in_sec=a_in))
        remaining -= take
        budget -= take
    # Photos and documents from archives: slow zoom/pan, ~8 s each.
    for i, a in enumerate(photos):
        if remaining <= 4:
            break
        take = min(8.0, remaining - 2)
        segs.append(compose.Segment("image", c.local(a["storage_path"]), take, pan="right" if i % 2 else "left"))
        remaining -= take
    # generated cards fill the rest, changing every ~16 s
    sentences = [x["text"] for x in (s["sentence_timings"] or [])]
    docs = list(s["on_screen_text"])
    pool: list[tuple[str, object]] = [("doc", i) for i in range(len(docs))]
    if timeline:
        pool.append(("timeline", None))
    # statement cards: substantive sentences (9-32 words) that carry a fact, spread across the scene
    cands = [t for t in sentences if 9 <= len(t.split()) <= 32]
    key_sents = [t for t in cands if re.search(r"\d|record|court|evidence|DNA|database|policy|warrant|laborator|affidavit|clear", t)]
    key_sents = (key_sents or cands)[:10]
    stmts = [("statement", t) for t in key_sents]
    mixed: list[tuple[str, object]] = []
    for i in range(max(len(pool), len(stmts))):  # interleave documents and statements
        if i < len(pool):
            mixed.append(pool[i])
        if i < len(stmts):
            mixed.append(stmts[i])
    pool = mixed or [("statement", s["title"])]
    n_cards = max(1, int(round(remaining / 16.0)))
    per = remaining / n_cards
    for i in range(n_cards):
        kind, val = pool[i % len(pool)]
        out = d / f"card-{i:02d}.png"
        if kind == "doc":
            lines = [("> " if k == val else "  ") + d for k, d in enumerate(docs)]
            cards.document_card(out, s["title"], lines, f"Scene {s['idx'] + 1} of {n_scenes}", seed=s["idx"] * 100 + i)
        elif kind == "timeline":
            items = [(t.split(":", 1)[0][:14], t.split(":", 1)[-1].strip()[:60]) for t in timeline[:6]]
            cards.timeline_card(out, items, highlight=min(len(items) - 1, s["idx"] * len(items) // max(1, n_scenes)),
                                title="Timeline", seed=i)
        else:
            cards.statement_card(out, str(val), s["chapter_title"] or "", seed=s["idx"] * 100 + i)
        segs.append(compose.Segment("image", out, per, pan="right" if i % 2 == 0 else "left"))
    # exact length: the last segment absorbs rounding
    total = sum(x.duration for x in segs)
    segs[-1].duration += dur - total
    h = sha(CARD_STYLE_VERSION, s["narration_hash"], dur, [str(x.path.name) for x in segs], [a["meta"]["blurred_key"] for a in clips],
            [(str(a["in_sec"]), str(a["out_sec"])) for a in clips], [a["storage_path"] for a in photos], map_key,
            c.project["selected_title"], timeline)
    return segs, h


def _case(c: Ctx) -> dict:
    if not c.project["idea_id"]:
        return {}
    r = db.q1("select case_info from ideas where id=%s", (c.project["idea_id"],))
    return (r or {}).get("case_info") or {}


def stage_assemble(c: Ctx) -> None:
    scenes = c.scenes()
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
            db.x("update scenes set visual_path=%s, visual_hash=%s, footage_type=%s where id=%s",
                 (key, h, _footage_type(s), s["id"]))
            parts.append(out)
        wavs.append(c.local(s["audio_path"]))
        c.progress("assemble", (k + 1) / len(scenes) * 0.6, f"scene {s['idx']} composed")

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


def credits(c: Ctx) -> str:
    rows = db.q("""select * from assets where project_id=%s and (selected or tier='gap') order by tier, created_at""", (c.pid,))
    out = ["Sources and footage credits:"]
    for s in db.q("select * from research_sources where project_id=%s order by length(ref), ref", (c.pid,)):
        out.append(f"- [{s['ref']}] {s['title']} — {s['publisher'] or ''} {s['url'] or ''}".rstrip())
    for a in rows:
        if a["tier"] == "gap":
            continue
        out.append("- " + rights.edit_note(a))
    if _case(c).get("location"):
        out.append("- Maps: Natural Earth (public domain); geocoding © OpenStreetMap contributors (ODbL)")
    m = db.q1("select * from assets where project_id=%s and kind='music' and selected", (c.pid,))
    out.append(f"- Music: {m['title']} — {m['source_name']} ({m['license']})" if m else
               "- Music: ambient bed generated by the app (no third-party music)")
    v = pricing.P["tts"].get(f"{c.project['voice_provider']}:{c.project['voice_id']}") or \
        next((x for k, x in pricing.P["tts"].items() if k.startswith(c.project["voice_provider"] + ":")), {})
    out.append(f"- Narration voice: {v.get('label', c.project['voice_provider'])} (AI-generated). {v.get('commercial_rights', '')}")
    return "\n".join(out)


def stage_package(c: Ctx) -> None:
    scenes = c.scenes()
    narration = "\n\n".join(s["narration"] for s in scenes)
    chapters = chapter_list(c)
    seo = agents.seo_pack(c.project, {"user_id": c.uid}, c.prov, c.model, c.fx, narration,
                          [{"t": agents.fmt_ts(t), "title": n} for t, n in chapters], idem=f"render:{c.rid}:seo")
    desc = apply_chapters(seo.description, chapters).replace("SOURCES_AND_CREDITS", credits(c))
    if seo.disclosure_line not in desc:
        desc += "\n\n" + seo.disclosure_line
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
        "", credits(c)]), encoding="utf-8")
    thumb = cards.thumbnail(c.work / f"thumb-v-{c.render['version']}.jpg", seo.thumbnail_text, "The Investigation, Explained"
                            if "cold case" in (c.project["title"] or "").lower() else (c.project["selected_title"] or "")[:40])
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


HANDLERS = {"script": stage_script, "voice": stage_voice, "footage": stage_footage, "blur": stage_blur,
            "assemble": stage_assemble, "package": stage_package}


def run_stage(job: dict, hb: queue.Heartbeat) -> None:
    c = Ctx(job, hb)
    stage = job["stage"]
    db.x("update renders set status='running', stage=%s where id=%s", (stage, c.rid))
    if HANDLERS[stage](c) == "wait":
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
