"""Animated Shorts channel: research -> character -> storyboard -> scenes -> key moments -> vertical Short."""
import subprocess
from pathlib import Path

import numpy as np


class HB:
    def update(self, **kw):
        pass


def _png(color, path: Path) -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (768, 1376), color)
    ImageDraw.Draw(im).rectangle([200, 500, 560, 1100], fill=(240, 200, 60))
    im.save(path)
    return path.read_bytes()


def test_animated_short(db, project, monkeypatch, tmp_path):
    from ycb.providers import genmedia, tts
    from ycb.schemas import AnimatedShort
    from ycb.stages import animated
    from ycb.config import settings
    from ycb.media.ff import probe
    from ycb.storage import store

    pid, ch = project["project"], project["channel"]
    db.execute("update channels set format='animated_shorts' where id=%s", (ch,))
    db.execute("insert into research_claims (project_id, claim, status, source_refs) values (%s,'A panic room has a steel door.','confirmed','{S1}')", (pid,))
    monkeypatch.setattr(settings, "gen_media", "mixed")
    monkeypatch.setattr(settings, "gemini_api_key", "test")
    calls = {"img": [], "vid": 0}

    def fake_image(prompt, **k):
        calls["img"].append((prompt, bool(k.get("refs")), k.get("aspect")))
        return _png((40 + len(calls["img"]) * 9 % 200, 90, 140), tmp_path / f"i{len(calls['img'])}.png")

    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=720x1280:r=24:d=4", "-pix_fmt", "yuv420p", str(clip)], check=True)

    def fake_video(prompt, seconds, **k):
        assert k["aspect"] == "9:16" and k["first_frame"]
        calls["vid"] += 1
        return clip.read_bytes()

    lines = [{"text": f"Line number {i} tells the story of the secret room in short words.", "scene": f"scene {i}",
              "character": i % 3 != 2, "camera": "wide", "animate": "door swings open" if i % 3 == 0 else ""} for i in range(12)]

    def fake_llm(**k):
        assert "Leo" in k["system"]
        return AnimatedShort.model_validate({"title": "He Built a Secret Room", "hook": "Nobody can find this room",
                                             "lines": lines, "description": "D.", "hashtags": ["panicroom"],
                                             "cover_line": 3, "cover_text": "SECRET ROOM"}), None
    monkeypatch.setattr(genmedia, "image", fake_image)
    monkeypatch.setattr(genmedia, "video", fake_video)
    monkeypatch.setattr(animated, "complete_json", fake_llm)
    monkeypatch.setattr(tts, "synth_sentence", lambda *a, **k: np.sin(np.linspace(0, 700, int(tts.SR * 2.4))).astype(np.float32) * 0.3)

    animated.run_animated_short({"id": "j", "project_id": pid, "payload": {}, "user_id": project["user"]}, HB())

    # character made once with the reference, then passed to every scene that shows him
    assert "Character reference" in calls["img"][0][0] and calls["img"][0][2] == "9:16"
    scene_calls = calls["img"][1:]
    assert len(scene_calls) == 12 and all(a == "9:16" for _, _, a in scene_calls)
    assert [r for _, r, _ in scene_calls].count(True) == 8
    assert calls["vid"] == 4  # exactly the planned key moments
    chrow = db.execute("select character from channels where id=%s", (ch,)).fetchone()["character"]
    assert chrow["name"] == "Leo" and store().exists(chrow["ref_path"])
    r = db.execute("select * from shorts where project_id=%s", (pid,)).fetchone()
    assert r["status"] == "succeeded" and r["kind"] == "animated", r["error"]
    assert r["title"] == "He Built a Secret Room #Shorts" and store().exists(r["cover_path"])
    info = probe(store().path(r["mp4_path"]))
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert (v["width"], v["height"]) == (1080, 1920) and 25 < float(info["format"]["duration"]) < 40
    assert sum(1 for x in r["lines"] if x["animated"]) == 4
    # a second Short on the channel reuses the same character (no new reference image)
    n_before = len(calls["img"])
    animated.ensure_character(animated.AnimCtx({"project_id": pid, "payload": {}}, HB()))
    assert len(calls["img"]) == n_before
