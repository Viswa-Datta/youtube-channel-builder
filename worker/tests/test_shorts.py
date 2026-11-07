"""Shorts: a finished documentary becomes vertical Shorts with their own script, voice, cuts and upload text."""
from pathlib import Path

import numpy as np


class HB:
    def update(self, **kw):
        pass


def test_shorts_made_from_documentary(db, project, monkeypatch):
    from ycb.agents import core as agents
    from ycb.media.ff import probe
    from ycb.providers import tts
    from ycb.schemas import ShortLine, ShortScript
    from ycb.stages import shorts
    from ycb.storage import project_key, store

    pid, sid = project["project"], project["scene"]
    db.execute("update scenes set narration=%s where id=%s", ("The tower rose fast. Then work stopped. " * 30, sid))
    rid = db.execute("insert into renders (project_id, version, status, has_audio) values (%s,1,'succeeded',true) returning id", (pid,)).fetchone()["id"]
    src = Path("/tmp/thumbsrc")
    pics = sorted(src.glob("*.jpg"))[:4]
    assert pics, "sample photos missing"
    for i, p in enumerate(pics):
        key = store().put(p, project_key(str(pid), "photos", f"p{i}.jpg"), "image/jpeg")
        db.execute("""insert into assets (project_id, scene_id, kind, tier, title, selected, storage_path, license, rights_checked, rights_checked_at)
                      values (%s,%s,'image','real',%s,true,%s,'Licensed',true,now())""", (pid, sid, f"Photo {i}", key))
    # a generic stock clip must never be offered to the Shorts Agent
    db.execute("""insert into assets (project_id, scene_id, kind, tier, title, selected, storage_path, license, rights_checked,
                  rights_checked_at, is_broll_label) values (%s,%s,'image','stock_broll','Doctor in hospital',true,'x','Licensed',true,now(),true)""",
               (pid, sid))
    seen = {}

    def fake_scripts(project, script, claims, media, n, gen, *a, **k):
        seen["media"] = media
        lines = [ShortLine(text=f"Line {i} about the tower and what happened next to everyone there.", asset=f"M{i % 4 + 1}")
                 for i in range(10)]
        return [ShortScript(title=f"Story {j}", hook="It was never finished", lines=lines, description="Desc.",
                            hashtags=["tower", "history"]) for j in range(n)]
    monkeypatch.setattr(agents, "shorts_scripts", fake_scripts)
    monkeypatch.setattr(tts, "synth_sentence", lambda *a, **k: np.sin(np.linspace(0, 900, int(tts.SR * 1.6))).astype(np.float32) * 0.3)

    job = {"id": "x", "render_id": rid, "payload": {"n": 2}, "project_id": pid}
    shorts.run_shorts(job, HB())

    assert all("Doctor" not in m["title"] for m in seen["media"])
    rows = db.execute("select * from shorts where project_id=%s order by idx", (pid,)).fetchall()
    assert [r["status"] for r in rows] == ["succeeded", "succeeded"], [r["error"] for r in rows]
    r = rows[0]
    assert r["title"].endswith("#Shorts") and "#Shorts" in r["description"]
    mp4 = store().path(r["mp4_path"]) if hasattr(store(), "path") else None
    info = probe(mp4)
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert (v["width"], v["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert 15 < float(info["format"]["duration"]) < 60
    assert len(r["lines"]) == 10 and r["lines"][0]["start"] < r["lines"][1]["start"]
    assert store().exists(r["srt_path"])
    # remaking one Short keeps its slot number and the other Short
    shorts.run_shorts({**job, "payload": {"idx": [1]}}, HB())
    assert db.execute("select count(*) n from shorts where project_id=%s", (pid,)).fetchone()["n"] == 2
