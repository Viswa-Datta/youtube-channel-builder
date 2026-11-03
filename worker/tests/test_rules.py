"""Rights rules, publish gate, ledger idempotency, hard cap, missing keys, agent validation."""
import json

import psycopg
import pytest


def _asset(db, p, **kw):
    cols = {"project_id": p["project"], "scene_id": p["scene"], "kind": "clip", "tier": "real", "license": "Do Not Use", **kw}
    keys = ",".join(cols)
    db.execute(f"insert into assets ({keys}) values ({','.join(['%s'] * len(cols))})", list(cols.values()))


# ---------------------------------------------------------------- rights test (spec: section 13)
def test_unsourced_clip_cannot_be_verified_real(db, project):
    with pytest.raises(psycopg.errors.CheckViolation, match="verified_real_requires_source"):
        _asset(db, project, verified_real=True, rights_checked=True, license="Public Domain")


def test_stock_is_never_real(db, project):
    with pytest.raises(psycopg.errors.CheckViolation, match="stock_never_real"):
        _asset(db, project, tier="stock_broll", license="Licensed", verified_real=True, is_broll_label=True,
               source_url="https://pexels.com/x", agency="Pexels", release_date="2024-01-01", rights_checked=True)
    with pytest.raises(psycopg.errors.CheckViolation, match="stock_never_real"):
        _asset(db, project, tier="stock_broll", license="Licensed", is_broll_label=False)  # must carry the B-roll label


def test_do_not_use_never_on_timeline(db, project):
    with pytest.raises(psycopg.errors.CheckViolation, match="do_not_use_never_selected"):
        _asset(db, project, tier="stock_broll", license="Do Not Use", is_broll_label=True, selected=True)


def test_real_on_timeline_needs_rights_check(db, project):
    with pytest.raises(psycopg.errors.CheckViolation, match="selected_real_needs_rights"):
        _asset(db, project, license="Public Domain", selected=True)


def test_fully_sourced_real_clip_is_allowed(db, project):
    _asset(db, project, license="Public Domain", verified_real=True, rights_checked=True, selected=True,
           source_url="https://catalog.archives.gov/id/123", agency="US National Archives", release_date="1971-05-01")


def test_rights_suggestions():
    from ycb.footage.rights import can_verify_real, suggest_license
    assert suggest_license(source_url="https://youtube.com/watch?v=x", agency="Some Channel").license == "Do Not Use"
    assert suggest_license(source_url="https://x.gov/a", agency="Leaked", rights_text="leaked bodycam").license == "Do Not Use"
    assert suggest_license(source_url="https://example.org", agency="City Police Department").license == "Public Record with release terms"
    assert suggest_license(source_url="https://archive.org/details/x", agency=None, licenseurl="http://creativecommons.org/publicdomain/mark/1.0/").license == "Public Domain"
    assert "source is a third-party video host" in " ".join(can_verify_real({"tier": "real", "source_url": "https://youtu.be/x", "agency": "a",
                                                                             "release_date": "2020-01-01", "rights_checked": True, "license": "Public Domain"}))


def test_confirmed_claim_needs_source(db, project):
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("insert into research_claims (project_id, claim, status) values (%s,'x','confirmed')", (project["project"],))


# ---------------------------------------------------------------- export + publish gate
def test_silent_render_cannot_succeed(db, project):
    rid = db.execute("insert into renders (project_id, version) values (%s,1) returning id", (project["project"],)).fetchone()["id"]
    with pytest.raises(psycopg.errors.RaiseException, match="silent export"):
        db.execute("update renders set status='succeeded', has_audio=false where id=%s", (rid,))


def test_publish_blocked_without_checklist(db, project):
    rid = db.execute("insert into renders (project_id, version, status, has_audio) values (%s,1,'succeeded',true) returning id",
                     (project["project"],)).fetchone()["id"]
    with pytest.raises(psycopg.errors.RaiseException, match="publish blocked"):
        db.execute("insert into publishes (project_id, render_id, user_id) values (%s,%s,%s)", (project["project"], rid, project["user"]))
    # the manual-download path is always allowed (it records intent, uploads nothing)
    db.execute("insert into publishes (project_id, render_id, user_id, status) values (%s,%s,%s,'manual')", (project["project"], rid, project["user"]))


def test_title_and_chapter_rules(db, project):
    rid = db.execute("insert into renders (project_id, version, status, has_audio) values (%s,1,'succeeded',true) returning id",
                     (project["project"],)).fetchone()["id"]
    db.execute("update projects set selected_title=%s, description=%s, disclosure_line='AI narration.' where id=%s",
               ("x" * 61, "Intro\n0:00 A\n1:00 B\n2:00 C\nAI narration.", project["project"]))
    items = {i["key"]: i["pass"] for i in db.execute("select publish_checklist(%s,%s) c", (project["project"], rid)).fetchone()["c"]["items"]}
    assert items["title_length"] is False and items["chapters_in_description"] is True and items["disclosure"] is True


# ---------------------------------------------------------------- ledger + cap
def test_ledger_is_idempotent(db, project):
    from ycb import ledger
    k = dict(user_id=str(project["user"]), stage="voice", provider="openai", unit_type="characters", cost_usd=0.3, idempotency_key="voice:x:0:h")
    assert ledger.record(**k) is True
    assert ledger.record(**k) is False  # a retry does not double-charge
    assert db.execute("select count(*) n from cost_ledger where idempotency_key='voice:x:0:h'").fetchone()["n"] == 1


def test_hard_cap_pauses_job(db, project):
    from ycb import main, queue
    db.execute("update users set monthly_cap_usd=0 where id=%s", (project["user"],))
    j = queue.enqueue(user_id=str(project["user"]), kind="ideas", payload={"channel_id": str(project["channel"]), "tier": "standard"})
    assert main.run_one(["ideas"]) is True
    row = db.execute("select status, last_error from jobs where id=%s", (j["id"],)).fetchone()
    assert row["status"] == "paused_cap" and "hard cap" in row["last_error"]
    assert db.execute("select count(*) n from cost_ledger where user_id=%s", (project["user"],)).fetchone()["n"] == 0


def test_missing_key_stops_and_lists_it(db, project):
    from ycb import main, queue
    j = queue.enqueue(user_id=str(project["user"]), kind="ideas", payload={"channel_id": str(project["channel"]), "tier": "standard"})
    main.run_one(["ideas"])
    row = db.execute("select status, last_error, attempts from jobs where id=%s", (j["id"],)).fetchone()
    assert row["status"] == "failed" and "OPENAI_API_KEY" in row["last_error"] and row["attempts"] == 1
    assert db.execute("select count(*) n from ideas where channel_id=%s", (project["channel"],)).fetchone()["n"] == 0  # nothing invented


# ---------------------------------------------------------------- agents: validation, ledger math, no invented sources
def test_agent_output_validated_and_ledger_math(db, project, monkeypatch):
    from ycb import pricing
    from ycb.providers import llm
    from ycb.schemas import IdeaBatch
    good = (llm.REPO_ROOT / "fixtures/quiet-case-file/ideas.json").read_text()
    calls = {"n": 0}

    def fake(model, system, user, schema, name):
        calls["n"] += 1
        return ('{"ideas": []}' if calls["n"] == 1 else good), 2000, 6000  # first answer invalid -> one corrective retry

    monkeypatch.setitem(llm.PROVIDERS, "openai", fake)
    obj, usage = llm.complete_json(out=IdeaBatch, agent="ideas", provider="openai", model="gpt-4o-mini", system="s", user="u",
                                   user_id=str(project["user"]), idem="t:ideas:1")
    assert len(obj.ideas) == 24 and calls["n"] == 2
    row = db.execute("select * from cost_ledger where idempotency_key='t:ideas:1'").fetchone()
    assert row["units_in"] == 4000 and row["units_out"] == 12000 and row["model"] == "gpt-4o-mini"
    assert abs(float(row["cost_usd"]) - pricing.llm_cost("openai", "gpt-4o-mini", 4000, 12000)) < 1e-9


def test_agent_gives_up_after_retry(project, monkeypatch):
    from ycb.providers import llm
    from ycb.schemas import IdeaBatch
    monkeypatch.setitem(llm.PROVIDERS, "openai", lambda *a: ('{"ideas": [{"title": "x"}]}', 10, 10))
    with pytest.raises(llm.AgentOutputInvalid):
        llm.complete_json(out=IdeaBatch, agent="ideas", provider="openai", model="gpt-4o-mini", system="s", user="u",
                          user_id=str(project["user"]), idem="t:ideas:bad")


def test_research_cannot_cite_unretrieved_sources(db, project, monkeypatch):
    from ycb.agents import core
    from ycb.providers import llm
    fake_research = {"summary": "s", "claims": [
        {"claim": "a", "status": "confirmed", "source_refs": ["S1"]},
        {"claim": "b", "status": "confirmed", "source_refs": ["S99"]},
        {"claim": "c", "status": "unknown", "source_refs": []}]}
    monkeypatch.setitem(llm.PROVIDERS, "openai", lambda *a: (json.dumps(fake_research), 10, 10))
    monkeypatch.setattr(core, "_retrieve", lambda p, q, fx, idea=None: [{"title": "t", "url": "https://x.gov", "tier": "agency_release", "publisher": "x"}])
    p = db.execute("select * from projects where id=%s", (project["project"],)).fetchone()
    with pytest.raises(core.BusinessRuleViolation, match="S99"):
        core.research(p, {"user_id": str(project["user"])}, "openai", "gpt-4o-mini", None)


def test_titles_must_be_under_60():
    from pydantic import ValidationError
    from ycb.schemas import SeoPack
    with pytest.raises(ValidationError, match="under 60"):
        SeoPack(titles=["a" * 60, "b", "c"], description="d" * 250, tags=["a", "b", "c", "d", "e"], thumbnail_concept="x",
                thumbnail_prompt="x", thumbnail_text="x", disclosure_line="Narration is AI generated here.")


# ---------------------------------------------------------------- captions
def test_srt_timings_and_pii_scrub():
    from ycb.media.captions import build_srt
    srt = build_srt([{"text": "Call 555-123-4567 about John Doe at 12 Elm Street.", "start": 1.0, "end": 4.5}], omit_terms=["John Doe"])
    assert "00:00:01,000 -->" in srt and "555-123-4567" not in srt and "John Doe" not in srt and "Elm Street" not in srt


def test_estimator_free_is_zero_and_standard_under_guardrail():
    from ycb import pricing
    for m in (8, 12, 20, 30):
        assert pricing.estimate("full", "free", m)["total_usd"] == 0
        assert pricing.estimate("full", "standard", m)["guardrail_violation"] is None
