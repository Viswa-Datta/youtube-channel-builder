"""Footage hunt -> help requests -> Footage Desk pause. All network calls are replaced with canned results."""
import pytest
from psycopg.types.json import Jsonb


class HB:
    def update(self, **kw):
        pass


@pytest.fixture()
def setup(db, project, monkeypatch):
    from ycb.footage import discovery, sources
    from ycb.footage.discovery import Evidence

    pid = project["project"]
    idea = db.execute("""insert into ideas (channel_id, title, hook, angle, demand_score, competition_score, feasibility_score,
                         case_info, footage_evidence, case_verified)
                         values (%s,'The Petito case','h','a',8,5,8,%s,%s,true) returning id""",
                      (project["channel"], Jsonb({"case_name": "Gabby Petito case", "location": "Moab, Utah",
                                                   "agency": "Moab City Police Department"}),
                       Jsonb([{"kind": "find_only", "source": "YouTube", "title": "Moab Police bodycam Petito traffic stop",
                               "url": "https://www.youtube.com/watch?v=abc", "official": True, "channel": "Moab City Police",
                               "published": "2021-09-16", "rights_note": "agency channel"}]))).fetchone()["id"]
    db.execute("update projects set idea_id=%s where id=%s", (idea, pid))
    db.execute("update scenes set title='Bodycam traffic stop', draft='Moab police stop the van' where id=%s", (project["scene"],))
    s1 = db.execute("insert into scenes (project_id, idx, title, draft) values (%s,1,'The search','Search of the park') returning id", (pid,)).fetchone()["id"]
    for idx, tier, q in [(0, "real", "Moab bodycam traffic stop"), (1, "real", "Grand Teton search footage"),
                         (1, "news_document", "Grand Teton map")]:
        db.execute("insert into footage_plan (project_id, scene_idx, priority, tier, description, search_query, source_hint) values (%s,%s,1,%s,%s,%s,'agency portal')",
                   (pid, idx, tier, f"need {q}", q))
    rid = db.execute("insert into renders (project_id, version) values (%s,1) returning id", (pid,)).fetchone()["id"]
    yt = [Evidence("find_only", "YouTube", "Moab Police bodycam part 2", "https://www.youtube.com/watch?v=def", channel="Moab City Police",
                   official=True, published="2021-09-16", license="Public Record with release terms"),
          Evidence("find_only", "YouTube", "REACTION to bodycam", "https://www.youtube.com/watch?v=zzz", channel="Some Streamer", official=False)]
    monkeypatch.setattr(discovery, "youtube", lambda q, n=8, agency=None: yt if "Moab" in q else [])
    monkeypatch.setattr(discovery, "wikimedia_commons", lambda q, n=8, media="video": (
        [Evidence("downloadable", "Wikimedia Commons", "Grand Teton map", "https://commons.wikimedia.org/wiki/File:x.png",
                  media_url="https://upload.wikimedia.org/x.png", license="Public Domain", media_type="image")] if media == "image" else []))
    monkeypatch.setattr(sources, "search_scene", lambda need: [])
    from ycb.config import settings
    monkeypatch.setattr(settings, "footage_mode", "assisted")
    return {"pid": pid, "rid": rid, "s0": project["scene"], "s1": s1}


def run_footage(rid):
    from ycb.stages import pipeline
    c = pipeline.Ctx({"render_id": str(rid), "stage": "footage"}, HB())
    return pipeline.stage_footage(c)


def test_footage_hunt_creates_requests_and_pauses(db, setup):
    assert run_footage(setup["rid"]) == "wait"
    reqs = db.execute("select * from help_requests where project_id=%s order by created_at", (setup["pid"],)).fetchall()
    saves = [r for r in reqs if r["kind"] == "save_clip"]
    # official videos only (idea evidence + live search), never the third-party reaction upload
    assert {r["source_url"] for r in saves} == {"https://www.youtube.com/watch?v=abc", "https://www.youtube.com/watch?v=def"}
    assert all(r["scene_id"] == setup["s0"] for r in saves)  # matched to the bodycam scene by its words
    # scene 1's real need found nothing -> GAP card + a find_source request
    finds = [r for r in reqs if r["kind"] == "find_source"]
    assert len(finds) == 1 and finds[0]["scene_id"] == setup["s1"]
    assert db.execute("select count(*) n from assets where scene_id=%s and tier='gap'", (setup["s1"],)).fetchone()["n"] == 1
    # the Commons map is a candidate waiting for the user's rights check, not auto-selected
    img = db.execute("select * from assets where scene_id=%s and kind='image'", (setup["s1"],)).fetchone()
    assert img["tier"] == "news_document" and not img["selected"] and not img["rights_checked"]


def test_retry_does_not_duplicate(db, setup):
    run_footage(setup["rid"])
    db.execute("update footage_plan set status='planned' where project_id=%s", (setup["pid"],))  # simulate a re-run
    run_footage(setup["rid"])
    n = db.execute("select count(*) n from help_requests where project_id=%s", (setup["pid"],)).fetchone()["n"]
    assert n == 3
    assert db.execute("select count(*) n from assets where project_id=%s and kind='image'", (setup["pid"],)).fetchone()["n"] == 1


def test_no_pause_when_nothing_needs_the_user(db, setup):
    run_footage(setup["rid"])
    db.execute("update help_requests set status='skipped' where project_id=%s", (setup["pid"],))
    db.execute("update assets set meta = meta || '{\"dismissed\":\"1\"}' where project_id=%s and kind='image'", (setup["pid"],))
    db.execute("update footage_plan set status='planned' where project_id=%s", (setup["pid"],))
    assert run_footage(setup["rid"]) is None


def test_run_stage_marks_render_waiting(db, setup):
    from ycb.stages import pipeline
    pipeline.run_stage({"render_id": str(setup["rid"]), "stage": "footage"}, HB())
    r = db.execute("select status, stage from renders where id=%s", (setup["rid"],)).fetchone()
    assert (r["status"], r["stage"]) == ("waiting_user", "footage_desk")
    # and no blur job was queued: the user continues from the desk
    assert db.execute("select count(*) n from jobs where render_id=%s and stage='blur'", (setup["rid"],)).fetchone()["n"] == 0


def test_help_request_done_needs_asset(db, setup):
    import psycopg
    run_footage(setup["rid"])
    rid = db.execute("select id from help_requests where kind='save_clip' limit 1").fetchone()["id"]
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("update help_requests set status='done' where id=%s", (rid,))


def test_45_and_60_minute_lengths_allowed(db, user):
    for m in (45, 60):
        db.execute("insert into channels (user_id, niche, target_minutes) values (%s,'x',%s)", (user, m))
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("insert into channels (user_id, niche, target_minutes) values (%s,'x',50)", (user,))


def test_visual_plan_chunks_and_fills_skipped_scenes(monkeypatch):
    from types import SimpleNamespace as NS
    from ycb.agents import core
    from ycb.schemas import FootageNeed, FootagePlan
    calls = []

    def fake(out, user, **kw):  # never plans scene 5, and plans scene 2 only when asked a second time
        import re as _re
        idxs = [int(x) for x in _re.search(r"scene numbers: \[([\d, ]+)\]", user).group(1).split(",")]
        calls.append(idxs)
        keep = [i for i in idxs if i != 5 and not (i == 2 and len(calls) == 1)]
        if not keep:
            raise ValueError("model returned nothing usable")
        needs = [FootageNeed(scene_idx=i, priority=1, tier="real", description="d", search_query="q", source_hint="h") for i in keep]
        while len(needs) < 3:
            needs.append(FootageNeed(scene_idx=keep[0], priority=3, tier="stock_broll", description="b", search_query="q", source_hint="h"))
        return FootagePlan(needs=needs), None
    monkeypatch.setattr(core, "complete_json", fake)
    monkeypatch.setattr(core.db, "q1", lambda *a, **k: {"case_info": {"case_name": "Test case"}})
    draft = NS(scenes=[NS(idx=i, title=f"S{i}", draft="x") for i in range(9)])
    plan = core.visual_plan({"id": "p", "title": "T", "idea_id": "i", "channel_id": "c"}, {"user_id": "u"}, draft, "gemini", "m", None)
    assert {n.scene_idx for n in plan.needs} == set(range(9))
    assert calls[0] == [0, 1, 2, 3] and [2] in calls  # grouped, then re-asked only for the skipped scene
    fb = [n for n in plan.needs if n.scene_idx == 5]
    assert len(fb) == 1 and fb[0].tier == "news_document" and "Test case" in fb[0].search_query


def test_short_narration_is_topped_up(monkeypatch):
    from ycb.agents import core
    from ycb.schemas import SceneNarration
    seen = []

    def fake(out, user, idem, **kw):
        seen.append(idem)
        n = 300 if not idem.endswith(("more0", "more1", "more2")) else 150
        return SceneNarration(idx=1, narration=" ".join(["word"] * n)), None
    monkeypatch.setattr(core, "complete_json", fake)
    text = core.expand_scene({"id": "p", "title": "T"}, {"idx": 1, "title": "S", "draft": "d"}, 533, [], "gemini", "m", None, "u", "r")
    assert len(text.split()) >= 0.9 * 533 and len(seen) == 3  # first draft + two continuations



def test_auto_footage_selects_only_licence_checked_media(db, project, monkeypatch):
    from ycb.footage import discovery, sources
    from ycb.footage.discovery import Evidence
    from ycb.stages import pipeline
    pid = project["project"]
    idea = db.execute("""insert into ideas (channel_id, title, hook, angle, demand_score, competition_score, feasibility_score, case_info)
                         values (%s,'Panama Canal','h','a',7,5,8,%s) returning id""",
                      (project["channel"], Jsonb({"case_name": "Panama Canal"}))).fetchone()["id"]
    db.execute("update projects set idea_id=%s where id=%s", (idea, pid))
    db.execute("update scenes set title='The locks' where id=%s", (project["scene"],))
    db.execute("insert into footage_plan (project_id, scene_idx, priority, tier, description, search_query, source_hint) values (%s,0,1,'real','locks','Panama Canal locks','Commons')", (pid,))
    rid = db.execute("insert into renders (project_id, version) values (%s,1) returning id", (pid,)).fetchone()["id"]
    pool = [Evidence("downloadable", "Wikimedia Commons", "Panama Canal locks.webm", "https://commons/x1", media_url="https://u/x1.webm",
                     channel="US Navy", license="Public Domain", media_type="video", meta={"license_short": "Public domain"}),
            Evidence("downloadable", "Wikimedia Commons", "Panama Canal aerial.jpg", "https://commons/x2", media_url="https://u/x2.jpg",
                     channel="J. Doe", license="Licensed", media_type="image", meta={"license_short": "CC BY 4.0", "thumb_url": "https://u/t2.jpg"}),
            Evidence("downloadable", "NASA", "Panama Canal from orbit", "https://images.nasa.gov/details/abc", channel="NASA JSC",
                     license="Public Domain", media_type="image", meta={"nasa_manifest": "https://m/abc", "license_short": "Public domain (NASA)"})]
    calls = []
    monkeypatch.setattr(discovery, "free_media", lambda q, n=6, words=None: calls.append(q) or list(pool))
    monkeypatch.setattr(sources, "search_scene", lambda need: [])
    from ycb.config import settings
    monkeypatch.setattr(settings, "footage_mode", "auto")
    c = pipeline.Ctx({"render_id": str(rid), "stage": "footage"}, HB())
    assert pipeline.stage_footage(c) is None  # never pauses
    rows = db.execute("select * from assets where project_id=%s and tier='real' order by created_at", (pid,)).fetchall()
    assert len(rows) == 3 and all(r["selected"] and r["rights_checked"] and r["meta"]["auto_rights"] for r in rows)
    assert {r["license"] for r in rows} == {"Public Domain", "Licensed"} and all(r["agency"] for r in rows)
    assert db.execute("select count(*) n from help_requests where project_id=%s", (pid,)).fetchone()["n"] == 0
    # publish checklist sees every selected item as sourced and rights-checked
    chk = db.execute("select publish_checklist(%s,%s) c", (pid, rid)).fetchone()["c"]
    items = {i["key"]: i for i in chk["items"]}
    assert items["rights_checked"]["pass"] and items["footage_logged"]["pass"]
    # re-running does not duplicate
    pipeline.stage_footage(c)
    assert db.execute("select count(*) n from assets where project_id=%s and tier='real'", (pid,)).fetchone()["n"] == 3


def test_licence_filter_and_presets():
    from ycb.agents.niches import preset_for
    from ycb.footage.discovery import license_ok
    assert license_ok("CC BY 4.0") and license_ok("Public domain") and license_ok("CC0")
    assert not license_ok("CC BY-SA 4.0") and not license_ok("CC BY-NC 2.0") and not license_ok("CC BY-ND 3.0") and not license_ok("")
    assert preset_for("F1 racing")["key"] == "motorsport"
    assert preset_for("Architectural marvels")["key"] == "architecture"
    assert preset_for("Geopolitics")["key"] == "geopolitics"
    assert preset_for("True Crime Documentary")["key"] == "true_crime"
    assert preset_for("Cooking")["key"] == "generic"
