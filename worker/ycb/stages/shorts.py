"""YouTube Shorts for a finished documentary.

The Shorts Agent writes n separate 45-55 s scripts from the documentary's own research and script (a hook in the first
two seconds, one surprising story each, a last line pointing to the full documentary) and picks, line by line, which of
the video's photos/clips is on screen, or asks for an AI scale-model illustration when nothing real shows it. Each Short
is voiced, cut vertically (9:16) with fast camera moves, and gets a big hook title, "AI illustration" marks and an end
prompt. Captions are a separate .srt file (never burned in), like the long video.
"""
from __future__ import annotations

import time
import wave
from pathlib import Path

import numpy as np

from .. import db, ledger, pricing, queue
from ..agents import core as agents
from ..config import settings
from ..media import captions, compose, vertical
from ..media.ff import duration, run
from ..providers import tts
from ..storage import project_key, store
from .pipeline import CREDITS_PAGE, Ctx, sha

GEN_PER_SHORT = 3       # AI illustrations per Short at most
MAX_HOLD = 4.2          # seconds a picture stays on screen before cutting to the next
LINE_GAP = 0.16         # pause between spoken lines (Shorts are fast)


class ShortCtx(Ctx):
    """Render context bound to the documentary's latest finished render, without touching that render's status."""

    def progress(self, stage: str, frac: float, note: str = "") -> None:
        if hasattr(self.hb, "check"):
            self.hb.check()
        self.hb.update(progress=round(max(0.0, min(1.0, frac)) * 100, 1), note=note)


def _media(c: Ctx) -> list[dict]:
    """The documentary's own pictures (no generic stock clips), with an id the agent can refer to."""
    rows = db.q("""select a.id, a.kind, a.tier, a.title, a.storage_path, a.meta, a.in_sec, a.out_sec, s.idx as scene_idx
                   from assets a left join scenes s on s.id = a.scene_id
                   where a.project_id=%s and a.selected and a.tier in ('real','news_document','generated')
                     and ((a.kind='image' and a.storage_path is not null) or (a.kind='clip' and a.meta ? 'blurred_key'))
                   order by a.tier='generated', s.idx nulls last, a.created_at""", (c.pid,))
    out = []
    for i, r in enumerate(rows):
        ai = r["tier"] == "generated"
        title = (r["title"] or "").replace("AI illustration: ", "")
        out.append({"id": f"M{i + 1}", "kind": "clip" if r["kind"] == "clip" else "photo",
                    "title": ("AI scale model: " if ai else "") + title, "row": r, "ai": ai, "uses": 0})
    return out


def _voice(c: Ctx, lines: list[str], out_wav: Path) -> tuple[list[dict], float, int]:
    """Each line voiced separately, joined with short pauses: exact per-line timings for the cuts."""
    p = c.project
    provider, voice = p["voice_provider"], p["voice_id"]
    if provider != "kokoro" and settings.missing_for(provider):
        provider, voice = "kokoro", "am_michael"
    model = {"openai": "tts-1", "elevenlabs": "eleven_multilingual_v2", "google": "chirp3-hd"}.get(provider)
    speed = float(p["voice_speed"] or 1.0) * 1.06  # a touch quicker than the documentary
    chars = sum(len(x) for x in lines)
    cost = pricing.tts_cost(provider, voice if provider == "kokoro" else model, chars)
    ledger.ensure_within_cap(c.uid, cost, "a Short's voiceover")
    t0 = time.time()
    buf = [np.zeros(int(0.12 * tts.SR), dtype=np.float32)]
    t, timings = 0.12, []
    for k, text in enumerate(lines):
        a = tts.synth_sentence(provider, voice, speed, text, model).astype(np.float32)
        timings.append({"text": text, "start": round(t, 3), "end": round(t + len(a) / tts.SR, 3)})
        buf.append(a)
        t += len(a) / tts.SR
        gap = LINE_GAP if k < len(lines) - 1 else 0.6
        buf.append(np.zeros(int(gap * tts.SR), dtype=np.float32))
        t += gap
    audio = np.concatenate(buf)
    audio = audio / (float(np.max(np.abs(audio))) or 1.0) * 0.89
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tts.SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    ledger.record(user_id=c.uid, project_id=c.pid, render_id=c.rid, stage="shorts", agent="voice", provider=provider,
                  model=model or voice, unit_type="characters", units_in=chars, cost_usd=cost,
                  duration_ms=int((time.time() - t0) * 1000),
                  idempotency_key=f"shortvoice:{c.pid}:{sha(*lines, provider, voice, speed)[:24]}")
    return timings, len(audio) / tts.SR, chars


def _ai_image(c: Ctx, prompt: str, gen: dict) -> Path | None:
    if gen["off"] or gen["left"] <= 0:
        return None
    from ..providers import genmedia
    h = sha(c.pid, "short", prompt)[:16]
    out = c.work / "shorts" / "gen" / f"{h}.png"
    if out.exists():
        return out
    try:
        data = genmedia.image(prompt, user_id=c.uid, project_id=c.pid, render_id=c.rid, idem=f"gen:{h}:short")
    except genmedia.GenUnavailable:
        gen["off"] = True
        return None
    except Exception:  # noqa: BLE001 - a declined prompt just uses a real picture instead
        return None
    gen["left"] -= 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out


def _local_media(c: Ctx, m: dict) -> Path:
    r = m["row"]
    return c.local(r["meta"]["blurred_key"] if m["kind"] == "clip" else r["storage_path"])


def build_short(c: Ctx, script, idx: int, media: list[dict], gen: dict) -> dict:
    """Voice, cut and finish one Short. Returns the values for its row."""
    work = c.work / "shorts" / f"short-{idx}"
    work.mkdir(parents=True, exist_ok=True)
    texts = [ln.text for ln in script.lines]
    timings, total, _ = _voice(c, texts, work / "voice.wav")
    by_id = {m["id"]: m for m in media}
    in_short: set[str] = set()
    stills: dict[str, Path] = {}

    def still(path: Path) -> Path:
        key = str(path)
        if key not in stills:
            stills[key] = vertical.prepare_vstill(path, work / f"still-{len(stills)}.jpg")
        return stills[key]

    prev: list = [None]

    def pool_pick(near_scene: int | None) -> dict | None:
        """A picture not yet in this Short; when the video has few, the least-shown one (never the one just shown)."""
        free = [m for m in media if m["id"] not in in_short] or [m for m in media if m["id"] != prev[0]] or media
        if not free:
            return None
        m = min(free, key=lambda m: (m["id"] in in_short, m["uses"], abs((m["row"]["scene_idx"] or 0) - (near_scene or 0)), m["ai"]))
        prev[0] = m["id"]
        return m

    shots: list[vertical.VShot] = []
    lines_out = []
    last_scene = None
    for k, (ln, tm) in enumerate(zip(script.lines, timings)):
        start = 0.0 if k == 0 else tm["start"]
        end = timings[k + 1]["start"] if k + 1 < len(timings) else total
        seg_len = end - start
        pic, is_ai, chosen = None, False, None
        m = by_id.get(ln.asset) if ln.asset else None
        if m is None and ln.generate:
            g = _ai_image(c, ln.generate, gen)
            if g is not None:
                pic, is_ai = ("photo", still(g), 0.0), True
        if pic is None:
            m = m if m is not None and m["id"] not in in_short else pool_pick(last_scene)
            if m is not None:
                in_short.add(m["id"])
                m["uses"] += 1
                last_scene, chosen, prev[0] = m["row"]["scene_idx"], m, m["id"]
                path = _local_media(c, m)
                pic = ("clip", path, float(m["row"]["in_sec"] or 0)) if m["kind"] == "clip" else ("photo", still(path), 0.0)
                is_ai = m["ai"]
        lines_out.append({"text": ln.text, "start": tm["start"], "end": tm["end"], "asset_id": str(chosen["row"]["id"]) if chosen else None,
                          "ai": is_ai})
        # cut long lines into two or three shots so nothing lingers
        n_parts = max(1, round(seg_len / MAX_HOLD + 0.2))
        for part in range(n_parts):
            d = seg_len / n_parts
            if part > 0 or pic is None:
                m2 = pool_pick(last_scene)
                if m2 is not None:
                    in_short.add(m2["id"])
                    m2["uses"] += 1
                    p2 = _local_media(c, m2)
                    pic = ("clip", p2, float(m2["row"]["in_sec"] or 0)) if m2["kind"] == "clip" else ("photo", still(p2), 0.0)
                    is_ai = m2["ai"]
            if pic is None:
                raise RuntimeError("this video has no photos or clips to build a Short from")
            kind, path, in_sec = pic
            shots.append(vertical.VShot(kind, path, d, in_sec + part * d, move=len(shots),
                                        label="AI illustration" if is_ai else ""))
    shots[-1].duration += total - sum(s.duration for s in shots)
    parts = []
    for i, s in enumerate(shots):
        parts.append(vertical.shot_video(s, work / f"shot-{i:02d}.mp4", work))
        c.progress("shorts", 0.3 + 0.5 * (i + 1) / len(shots), f"Short {idx + 1}: shot {i + 1}/{len(shots)}")
    video = compose.concat_videos(parts, work / "video.mp4")
    m = db.q1("select storage_path from assets where project_id=%s and kind='music' and selected and storage_path is not null",
              (c.pid,))
    music = c.local(m["storage_path"]) if m else compose.ambient_bed(total, work / "bed.wav")
    audio, _ = compose.mix(work / "voice.wav", music, work / "mix.m4a", music_db=-18.0)
    accent = (255, 205, 40)
    hook = vertical.hook_png(script.hook or texts[0], work / "hook.png", accent)
    endp = vertical.end_png(work / "end.png", accent=accent)
    first_photo = next((s.path for s in shots if s.kind == "photo"), None)
    thumb = work / "cover.jpg"
    if first_photo is not None:
        vertical.cover_png(first_photo, script.hook or script.title, thumb, accent)
    out = vertical.finish(video, audio, hook, endp, total, work / f"short-{idx}.mp4",
                          cover=thumb if first_photo is not None else None)
    if first_photo is None:
        run(["-ss", "0.8", "-i", str(out), "-frames:v", "1", "-q:v", "3", str(thumb)])
    srt = work / f"short-{idx}.srt"
    srt.write_text(captions.build_srt(timings), encoding="utf-8")
    keys = {
        "mp4_path": store().put(out, project_key(c.pid, "shorts", f"short-{idx}.mp4"), "video/mp4"),
        "srt_path": store().put(srt, project_key(c.pid, "shorts", f"short-{idx}.srt"), "application/x-subrip"),
        "thumb_path": store().put(thumb, project_key(c.pid, "shorts", f"short-{idx}.jpg"), "image/jpeg"),
        "cover_path": project_key(c.pid, "shorts", f"short-{idx}.jpg"),
    }
    any_ai = any(x["ai"] for x in lines_out)
    desc = script.description.strip()
    tail = [f"Credits: {CREDITS_PAGE}{c.rid}"]
    if any_ai:
        tail.insert(0, "Scale-model scenes are AI illustrations (Google Gemini), marked on screen.")
    tags = " ".join(f"#{t}" for t in script.hashtags)
    return {**keys, "title": f"{script.title} #Shorts"[:100], "hook": script.hook, "narration": " ".join(texts),
            "description": "\n\n".join(x for x in [desc, "\n".join(tail), f"#Shorts {tags}".strip()] if x),
            "hashtags": script.hashtags, "lines": lines_out, "duration_sec": round(duration(out), 3)}


def run_shorts(job: dict, hb) -> None:
    """Job 'shorts': payload {n}; render_id = the documentary's finished render."""
    c = ShortCtx(job, hb)
    n = max(1, min(5, int(job["payload"].get("n", 2))))
    c.progress("shorts", 0.02, "reading the documentary")
    scenes = c.scenes()
    script = "\n\n".join(s["narration"] for s in scenes if s["narration"])
    claims = [r["claim"] for r in db.q("select claim from research_claims where project_id=%s order by claim", (c.pid,))]
    media = _media(c)
    if not media:
        raise agents.BusinessRuleViolation("this video has no photos or clips to build Shorts from")
    done = db.q("select idx, title from shorts where project_id=%s order by idx", (c.pid,))
    ids = [int(x) for x in job["payload"].get("idx") or []] or \
        list(range((max([d["idx"] for d in done]) + 1) if done else 0, ((max([d["idx"] for d in done]) + 1) if done else 0) + n))
    avoid = [d["title"] for d in done if d["title"] and d["idx"] not in ids]
    for i in ids:
        db.x("""insert into shorts (project_id, idx, status) values (%s,%s,'running')
                on conflict (project_id, idx) do update set status='running', error=null, updated_at=now()""", (c.pid, i))
    try:
        c.progress("shorts", 0.08, f"writing {len(ids)} Short scripts")
        scripts = agents.shorts_scripts(c.project, script, claims, [{k: m[k] for k in ("id", "kind", "title")} for m in media],
                                        len(ids), GEN_PER_SHORT, c.prov, c.model, c.uid, c.rid, avoid)
    except Exception as e:
        db.x("update shorts set status='failed', error=%s, updated_at=now() where project_id=%s and idx = any(%s)",
             (str(e)[:500], c.pid, ids))
        raise
    gen = {"off": settings.gen_media == "off" or not settings.gemini_api_key, "left": GEN_PER_SHORT * len(ids)}
    errors = []
    for k, (i, s) in enumerate(zip(ids, scripts)):
        c.progress("shorts", 0.15 + 0.8 * k / len(ids), f"making Short {k + 1} of {len(ids)}: {s.title}")
        try:
            vals = build_short(c, s, i, media, gen)
        except queue.JobCancelled:
            raise
        except Exception as e:  # noqa: BLE001 - one failed Short does not lose the others
            errors.append(f"Short {k + 1}: {e}")
            db.x("update shorts set status='failed', error=%s, updated_at=now() where project_id=%s and idx=%s",
                 (str(e)[:500], c.pid, i))
            continue
        cols = ", ".join(f"{k_}=%s" for k_ in vals)
        db.x(f"update shorts set {cols}, status='succeeded', error=null, updated_at=now() where project_id=%s and idx=%s",
             (*[db.jsonb(v) if k_ == "lines" else v for k_, v in vals.items()], c.pid, i))
    for i in ids[len(scripts):]:
        db.x("update shorts set status='failed', error='the Shorts Agent wrote fewer scripts' where project_id=%s and idx=%s",
             (c.pid, i))
    if errors and len(errors) == len(ids):
        raise RuntimeError("; ".join(errors)[:1500])
    c.progress("shorts", 1.0, "Shorts ready")
