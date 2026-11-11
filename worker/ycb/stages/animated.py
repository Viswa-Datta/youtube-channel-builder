"""Animated Shorts channel: one 45-60 s vertical Short per project, made entirely of AI 3D animated scenes.

1. Research: the same sourced research as documentaries (Wikipedia sections, news), so every fact is checked.
2. The channel's main character: an original character created once (a reference image kept on the channel) and
   passed to every scene, so the same person appears throughout.
3. Script and Storyboard Agent: 12-18 punchy lines, each with a scene, camera and (for ~4 key moments) a motion.
4. Gemini draws every scene in 9:16 (with the character reference) and animates the key moments from those frames.
5. Voice (free Kokoro by default), fast cuts (each line split into a wide and a punch-in angle), music bed, hook
   title, end prompt, designed cover as the first frames + separate cover image, captions as a separate .srt.
"""
from __future__ import annotations

import concurrent.futures as cf
from pathlib import Path

from .. import db, queue
from ..agents import core as agents
from ..agents import prompts
from ..config import settings
from ..media import captions, compose, vertical
from ..media.ff import duration
from ..providers import genmedia
from ..providers.llm import complete_json
from ..schemas import AnimatedShort
from ..storage import project_key, store
from .pipeline import sha
from .shorts import _voice

ANIMATED_CLIPS = 4       # key moments animated per Short
CLIP_SECONDS = 4
CUT = 2.3                # seconds per angle before cutting (fast Shorts pacing)
DEFAULT_CHARACTER = {
    "name": "Leo",
    "look": ("a friendly man in his early thirties with short wavy dark-brown hair, a neat short beard, warm light-brown "
             "skin and brown eyes, slim build, wearing a mustard-yellow zip hoodie over a white T-shirt, dark navy cargo "
             "trousers and white sneakers; expressive, curious face"),
}


class AnimCtx:
    """Context for an animated Short (no long-video render behind it)."""

    def __init__(self, job: dict, hb):
        self.job, self.hb = job, hb
        self.project = db.q1("select * from projects where id=%s", (job["project_id"],))
        self.channel = db.q1("select * from channels where id=%s", (self.project["channel_id"],))
        self.pid, self.uid, self.rid = str(self.project["id"]), str(self.project["user_id"]), None
        self.work = settings.work_dir / self.pid / "animated"
        self.work.mkdir(parents=True, exist_ok=True)
        self.prov, self.model, self.fx = agents.llm_choice(
            {"llm_provider": self.project["llm_provider"], "llm_model": self.project["llm_model"]}, self.project["tier"])

    def progress(self, frac: float, note: str = "") -> None:
        if hasattr(self.hb, "check"):
            self.hb.check()
        self.hb.update(progress=round(max(0.0, min(1.0, frac)) * 100, 1), note=note)

    def local(self, key: str) -> Path:
        p = self.work / "cache" / key.replace("/", "__")
        if not p.exists():
            store().get(key, p)
        return p


def ensure_character(c: AnimCtx) -> tuple[dict, bytes]:
    """The channel's recurring main character: created once, then reused for every Short."""
    ch = c.channel
    char = dict(ch.get("character") or {})
    if char.get("ref_path") and store().exists(char["ref_path"]):
        return char, c.local(char["ref_path"]).read_bytes()
    char = {**DEFAULT_CHARACTER, **{k: v for k, v in char.items() if v}}
    data = genmedia.image(
        f"Character reference: one full-body front view of {char['look']}, standing in a relaxed neutral pose, arms "
        f"slightly away from the body, plain light-grey studio background, even lighting",
        user_id=c.uid, project_id=c.pid, render_id=None, idem=f"character:{ch['id']}:{sha(char['look'])[:12]}",
        aspect="9:16", style=genmedia.ANIMATED_STYLE)
    p = c.work / "character.png"
    p.write_bytes(data)
    char["ref_path"] = store().put(p, f"channels/{ch['id']}/character-{sha(char['look'])[:8]}.png", "image/png")
    db.x("update channels set character=%s where id=%s", (db.jsonb(char), ch["id"]))
    return char, data


def write_script(c: AnimCtx, char: dict, claims: list[str]) -> AnimatedShort:
    idea = db.q1("select * from ideas where id=%s", (c.project["idea_id"],)) if c.project["idea_id"] else None
    user = (f"Short: {(idea or {}).get('title') or c.project['title']}\nKind: {(idea or {}).get('pillar') or '-'}\n"
            f"Hook idea: {(idea or {}).get('hook') or '-'}\nStory angle: {(idea or {}).get('angle') or '-'}\n\n"
            f"RESEARCH CLAIMS:\n" + "\n".join(claims[:90]))
    out, _ = complete_json(out=AnimatedShort, agent="script", provider=c.prov, model=c.model,
                           system=prompts.ANIMATED_SHORT.format(name=char["name"], look=char["look"], anim=ANIMATED_CLIPS),
                           user=user, user_id=c.uid, project_id=c.pid, stage="script",
                           idem=f"project:{c.pid}:animated:{sha(user)[:16]}:{c.job['payload'].get('version', 1)}")
    # exactly the planned number of animated moments (the agent sometimes marks more)
    marked = [k for k, ln in enumerate(out.lines) if ln.animate]
    for k in marked[ANIMATED_CLIPS:]:
        out.lines[k].animate = ""
    return out


def _scene_prompt(ln, char: dict, title: str) -> str:
    who = (f"{char['name']} ({char['look']}) is in the shot. " if ln.character else
           f"{char['name']} is not in this shot. ")
    return f"Vertical 9:16 {ln.camera} shot from a short animated story called '{title}'. {who}{ln.scene}"


def draw_scenes(c: AnimCtx, short: AnimatedShort, char: dict, ref: bytes) -> list[Path | None]:
    """All scenes in parallel; a scene the model declines is retried once with a plainer description."""
    out: list[Path | None] = [None] * len(short.lines)

    def one(k: int) -> tuple[int, Path | None]:
        ln = short.lines[k]
        p = c.work / f"scene-{k:02d}-{sha(ln.scene, ln.camera, str(ln.character))[:10]}.png"
        if p.exists():
            return k, p
        for attempt in range(2):
            prompt = _scene_prompt(ln, char, short.title) if attempt == 0 else f"{ln.camera} shot. {ln.scene}"
            try:
                data = genmedia.image(prompt, user_id=c.uid, project_id=c.pid, render_id=None,
                                      idem=f"anim:{c.pid}:{sha(prompt)[:16]}", aspect="9:16",
                                      style=genmedia.ANIMATED_STYLE, refs=[ref] if ln.character else None)
            except genmedia.GenUnavailable:
                raise
            except Exception:  # noqa: BLE001 - declined or flaky: try the plainer prompt, then fall back
                continue
            p.write_bytes(data)
            return k, p
        return k, None

    done = 0
    with cf.ThreadPoolExecutor(4) as ex:
        for fut in cf.as_completed([ex.submit(one, k) for k in range(len(short.lines))]):
            k, p = fut.result()
            out[k] = p
            done += 1
            c.progress(0.25 + 0.3 * done / len(short.lines), f"drawing scenes {done}/{len(short.lines)}")
    if not any(out):
        raise RuntimeError("no scene could be drawn (Gemini declined every prompt)")
    for k in range(len(out)):  # a missing scene reuses the nearest drawn one (shown from another angle)
        if out[k] is None:
            out[k] = next((out[j] for j in sorted(range(len(out)), key=lambda j: abs(j - k)) if out[j]), None)
    return out


def animate_moments(c: AnimCtx, short: AnimatedShort, scenes: list[Path]) -> dict[int, Path]:
    """Image-to-video for the key moments, starting from the drawn scene (so the character stays the same)."""
    todo = [k for k, ln in enumerate(short.lines) if ln.animate][:ANIMATED_CLIPS]
    clips: dict[int, Path] = {}
    if not todo or settings.gen_media == "off":
        return clips

    def one(k: int) -> tuple[int, Path | None]:
        ln = short.lines[k]
        p = c.work / f"clip-{k:02d}-{sha(ln.scene, ln.animate)[:10]}.mp4"
        if p.exists():
            return k, p
        try:
            data = genmedia.video(f"{ln.scene}. Action: {ln.animate}", CLIP_SECONDS, user_id=c.uid, project_id=c.pid,
                                  render_id=None, idem=f"animclip:{c.pid}:{k}:{sha(ln.scene, ln.animate)[:12]}",
                                  first_frame=scenes[k].read_bytes(), aspect="9:16", style=genmedia.ANIMATED_STYLE,
                                  motion="Natural character animation, gentle camera move.")
        except Exception:  # noqa: BLE001 - an unanimated moment just stays a still with a camera move
            return k, None
        p.write_bytes(data)
        return k, p

    with cf.ThreadPoolExecutor(4) as ex:
        for n, fut in enumerate(cf.as_completed([ex.submit(one, k) for k in todo]), 1):
            k, p = fut.result()
            if p is not None:
                clips[k] = p
            c.progress(0.55 + 0.15 * n / len(todo), f"animating key moments {n}/{len(todo)}")
    return clips


def run_animated_short(job: dict, hb) -> None:
    c = AnimCtx(job, hb)
    if c.channel.get("format") != "animated_shorts":
        raise agents.BusinessRuleViolation("this project's channel is not an Animated Shorts channel")
    if settings.gen_media == "off" or not settings.gemini_api_key:
        raise agents.BusinessRuleViolation("Animated Shorts need AI scenes: set GEMINI_API_KEY and GEN_MEDIA on the worker")
    db.x("""insert into shorts (project_id, idx, status, kind) values (%s,0,'running','animated')
            on conflict (project_id, idx) do update set status='running', error=null, kind='animated', updated_at=now()""",
         (c.pid,))
    try:
        _build(c)
    except queue.JobCancelled:
        db.x("update shorts set status='failed', error='cancelled' where project_id=%s and idx=0", (c.pid,))
        raise
    except Exception as e:
        db.x("update shorts set status='failed', error=%s, updated_at=now() where project_id=%s and idx=0",
             (str(e)[:500], c.pid))
        raise


def _build(c: AnimCtx) -> None:
    # 1. research (kept between remakes)
    c.progress(0.02, "researching the facts")
    if not db.q1("select 1 from research_claims where project_id=%s limit 1", (c.pid,)):
        agents.research(c.project, {**c.job, "user_id": c.uid}, c.prov, c.model, c.fx)
    claims = [r["claim"] for r in db.q("""select claim from research_claims where project_id=%s and status <> 'unknown'
                                          order by scene_idx nulls last, claim""", (c.pid,))]
    # 2. character, 3. script
    c.progress(0.12, "preparing the main character")
    char, ref = ensure_character(c)
    c.progress(0.18, "writing the script and storyboard")
    short = write_script(c, char, claims)
    # 4. scenes + key moments
    scenes = draw_scenes(c, short, char, ref)
    clips = animate_moments(c, short, scenes)
    # 5. voice
    c.progress(0.72, "recording the voice")
    texts = [ln.text for ln in short.lines]
    timings, total, _ = _voice(c, texts, c.work / "voice.wav")
    # 6. cut: each line = its scene; long lines cut between a wide and a punch-in angle; key moments use the clip
    shots: list[vertical.VShot] = []
    stills: dict[str, Path] = {}

    def still(p: Path) -> Path:
        if str(p) not in stills:
            stills[str(p)] = vertical.prepare_vstill(p, c.work / f"v-{len(stills):02d}.jpg")
        return stills[str(p)]

    for k, tm in enumerate(timings):
        start = 0.0 if k == 0 else tm["start"]
        end = timings[k + 1]["start"] if k + 1 < len(timings) else total
        seg = end - start
        if k in clips:
            clip_len = min(seg, max(1.0, duration(clips[k]) - 0.1))
            shots.append(vertical.VShot("clip", clips[k], clip_len, move=k))
            seg -= clip_len
            if seg < 0.4:
                shots[-1].duration += seg
                continue
        n = max(1, round(seg / CUT))
        for part in range(n):
            shots.append(vertical.VShot("photo", still(scenes[k]), seg / n, move=k + part, punch=part % 2 == 1))
    shots[-1].duration += total - sum(s.duration for s in shots)
    parts = []
    for i, s in enumerate(shots):
        parts.append(vertical.shot_video(s, c.work / f"shot-{i:02d}.mp4", c.work))
        c.progress(0.75 + 0.17 * (i + 1) / len(shots), f"cutting shot {i + 1}/{len(shots)}")
    video = compose.concat_videos(parts, c.work / "video.mp4")
    music = compose.ambient_bed(total, c.work / "bed.wav")
    audio, _ = compose.mix(c.work / "voice.wav", music, c.work / "mix.m4a", music_db=-19.0)
    accent = (255, 205, 40)
    cover = vertical.cover_png(scenes[short.cover_line], short.cover_text or short.hook or short.title,
                               c.work / "cover.jpg", accent)
    out = vertical.finish(video, audio, vertical.hook_png(short.hook or texts[0], c.work / "hook.png", accent),
                          vertical.end_png(c.work / "end.png", text="FOLLOW FOR MORE", accent=accent), total,
                          c.work / "short.mp4", cover=cover)
    srt = c.work / "short.srt"
    srt.write_text(captions.build_srt(timings), encoding="utf-8")
    c.progress(0.95, "saving")
    keys = {
        "mp4_path": store().put(out, project_key(c.pid, "shorts", "short-0.mp4"), "video/mp4"),
        "srt_path": store().put(srt, project_key(c.pid, "shorts", "short-0.srt"), "application/x-subrip"),
        "cover_path": store().put(cover, project_key(c.pid, "shorts", "cover-0.jpg"), "image/jpeg"),
    }
    keys["thumb_path"] = keys["cover_path"]
    tags = " ".join(f"#{t}" for t in short.hashtags)
    desc = "\n\n".join(x for x in [short.description.strip(),
                                   "Animated with AI (Google Gemini); facts from public sources.",
                                   f"#Shorts {tags}".strip()] if x)
    lines = [{"text": ln.text, "start": tm["start"], "end": tm["end"], "ai": True, "scene": ln.scene,
              "animated": k in clips} for k, (ln, tm) in enumerate(zip(short.lines, timings))]
    vals = {**keys, "title": f"{short.title} #Shorts"[:100], "hook": short.hook, "narration": " ".join(texts),
            "description": desc, "hashtags": short.hashtags, "lines": db.jsonb(lines),
            "duration_sec": round(duration(out), 3)}
    cols = ", ".join(f"{k}=%s" for k in vals)
    db.x(f"update shorts set {cols}, status='succeeded', error=null, kind='animated', updated_at=now() "
         f"where project_id=%s and idx=0", (*vals.values(), c.pid))
    db.x("update projects set selected_title=coalesce(selected_title, %s), status='review', updated_at=now() where id=%s",
         (short.title, c.pid))
    c.progress(1.0, f"Short ready ({len(short.lines)} scenes, {len(clips)} animated)")
