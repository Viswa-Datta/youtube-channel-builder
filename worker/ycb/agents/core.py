"""Agent job handlers: Channel, Ideas, Research, Script, Visual Director, Editor + SEO.
(The Voice Agent is TTS orchestration and lives in stages/pipeline.py.)

Each handler: load inputs from the DB -> one narrow LLM call per step -> schema validation ->
post-validation business rules -> write rows. If a step fails validation, nothing after it runs.
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, create_model

from .. import db, ledger, pricing
from ..footage import sources
from ..providers.llm import complete_json
from ..schemas import (CHANNEL_KIT_ITEMS, TIER_ORDER, ChannelKit, FootagePlan, IdeaBatch, Research, SceneNarration,
                       ScriptDraft, SeoPack, rank_score)
from . import prompts


class BusinessRuleViolation(RuntimeError):
    pass


def llm_choice(payload: dict, tier: str = "standard") -> tuple[str, str, str | None]:
    """(provider, model, fixture_set). Payload overrides the tier default from config/pricing.json."""
    if payload.get("llm_provider"):
        prov, model = payload["llm_provider"], payload.get("llm_model") or ""
    else:
        prov, model = pricing.P["tiers"][tier]["llm"].split(":", 1)
    return prov, model, (model if prov == "fixture" else None)


def _fx(fixture_set: str | None, name: str) -> str | None:
    return f"{fixture_set}/{name}.json" if fixture_set else None


# ------------------------------------------------------------------ Channel Agent
def channel_kit(job: dict) -> dict:
    p = job["payload"]
    ch = db.q1("select * from channels where id=%s", (p["channel_id"],))
    prov, model, fx = llm_choice(p, p.get("tier", "standard"))
    est = pricing.estimate("channel_kit", p.get("tier", "standard"))
    ledger.ensure_within_cap(job["user_id"], est["total_usd"], "Channel Agent")
    user = (f"Niche: {ch['niche']}\nSub-niche: {ch['sub_niche'] or '-'}\nAudience: {ch['audience'] or '-'}\n"
            f"Language: {ch['language']}\nTone: {ch['tone'] or '-'}\nTarget length: {ch['target_minutes']} minutes\n"
            f"Faceless: {ch['faceless']}\nStyle references (style only): {', '.join(ch['competitor_urls']) or '-'}\n"
            f"Seed ideas from the user: {json.dumps(p.get('seed') or {})}")
    kit, _ = complete_json(out=ChannelKit, agent="channel", provider=prov, model=model, system=prompts.CHANNEL, user=user,
                           user_id=job["user_id"], idem=f"job:{job['id']}:channel_kit", fixture=_fx(fx, "channel_kit"))
    ver = db.q1("select coalesce(max(version),0)+1 v from channel_kits where channel_id=%s", (ch["id"],))["v"]
    selected = {"name": kit.names[0].name, "handle": kit.handles[0], "pillars": kit.pillars, "cadence": kit.cadence}
    row = db.q1("insert into channel_kits (channel_id, version, data, selected) values (%s,%s,%s,%s) returning id",
                (ch["id"], ver, db.jsonb(kit.model_dump()), db.jsonb(selected)))
    db.x("update channels set name=coalesce(name,%s), cadence=%s where id=%s", (selected["name"], kit.cadence, ch["id"]))
    return {"kit_id": str(row["id"]), "version": ver}


def channel_kit_item(job: dict) -> dict:
    p = job["payload"]
    item = p["item"]
    if item not in CHANNEL_KIT_ITEMS:
        raise BusinessRuleViolation(f"unknown kit item {item}")
    kit = db.q1("select * from channel_kits where id=%s", (p["kit_id"],))
    field_info = ChannelKit.model_fields[item]
    Partial: type[BaseModel] = create_model(f"KitItem_{item}", **{item: (field_info.annotation, field_info)})
    prov, model, fx = llm_choice(p, p.get("tier", "standard"))
    user = f"Current kit:\n{json.dumps(kit['data'])}\n\nRegenerate only: {item}"
    res, _ = complete_json(out=Partial, agent="channel", provider=prov, model=model, system=prompts.CHANNEL_ITEM,
                           user=user, user_id=job["user_id"], idem=f"job:{job['id']}:kit_item",
                           fixture=_fx(fx, f"channel_kit_item_{item}"))
    data = dict(kit["data"])
    data[item] = res.model_dump(mode="json")[item]
    ChannelKit.model_validate(data)  # the whole kit must still be valid
    db.x("update channel_kits set data=%s where id=%s", (db.jsonb(data), kit["id"]))
    return {"item": item}


# ------------------------------------------------------------------ Ideas Agent
def ideas(job: dict) -> dict:
    p = job["payload"]
    ch = db.q1("select * from channels where id=%s", (p["channel_id"],))
    kit = db.q1("select * from channel_kits where channel_id=%s order by version desc limit 1", (ch["id"],))
    tier = p.get("tier", "standard")
    prov, model, fx = llm_choice(p, tier)
    ledger.ensure_within_cap(job["user_id"], pricing.estimate("ideas", tier)["total_usd"], "Ideas Agent")
    existing = [r["title"] for r in db.q("select title from ideas where channel_id=%s", (ch["id"],))]
    user = (f"Channel: {json.dumps((kit or {}).get('selected') or {})}\nNiche: {ch['niche']} / {ch['sub_niche'] or ''}\n"
            f"Audience: {ch['audience'] or '-'}\nTarget length: {ch['target_minutes']} min\nCount: {p.get('count', 25)}\n"
            f"Already have (do not repeat): {json.dumps(existing[:80])}")
    batch, _ = complete_json(out=IdeaBatch, agent="ideas", provider=prov, model=model, system=prompts.IDEAS, user=user,
                             user_id=job["user_id"], idem=f"job:{job['id']}:ideas", fixture=_fx(fx, "ideas"))
    est = pricing.estimate("full", tier, minutes=ch["target_minutes"])["total_usd"]
    n_batch = db.q1("select coalesce(max(batch),0)+1 b from ideas where channel_id=%s", (ch["id"],))["b"]
    ranked = sorted(batch.ideas, key=rank_score, reverse=True)
    for pos, i in enumerate(ranked):
        db.x("""insert into ideas (channel_id, batch, title, hook, angle, pillar, demand_score, competition_score,
                feasibility_score, est_cost_usd, rank_score, position, rationale)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
             (ch["id"], n_batch, i.title, i.hook, i.angle, i.pillar, i.demand_score, i.competition_score,
              i.footage_feasibility_score, est, rank_score(i), pos, i.rationale))
    return {"count": len(ranked), "batch": n_batch}


# ------------------------------------------------------------------ Research Agent
def _retrieve(project: dict, query: str, fixture_set: str | None) -> list[dict]:
    """Collect sources in tier order. Fixture mode reads a pre-verified list (URLs checked by hand)."""
    if fixture_set:
        from ..config import REPO_ROOT
        return json.loads((REPO_ROOT / "fixtures" / fixture_set / "sources.json").read_text())
    found: list[dict] = []
    for item in sources.courtlistener(query):
        found.append({**item, "tier": "court_record", "provider": "courtlistener"})
    for item in sources.gdelt_news(query, gov_only=True):
        found.append({**item, "tier": "agency_release", "provider": "gdelt"})
    for item in sources.gdelt_news(query):
        found.append({**item, "tier": "contemporaneous_news", "provider": "gdelt"})
    for c in sources.library_of_congress(query, rows=5):
        found.append({"title": c.title, "url": c.source_url, "publisher": "Library of Congress",
                      "published_at": c.release_date, "tier": "reference", "provider": "loc", "excerpt": ""})
    found.sort(key=lambda s: TIER_ORDER[s["tier"]])
    return found[:40]


def research(project: dict, job: dict, prov: str, model: str, fx: str | None) -> Research:
    idea = db.q1("select * from ideas where id=%s", (project["idea_id"],)) if project["idea_id"] else None
    query = (idea or {}).get("title") or project["title"]
    retrieved = _retrieve(project, query, fx)
    db.x("delete from research_sources where project_id=%s", (project["id"],))
    listing = []
    for n, s in enumerate(retrieved, 1):
        ref = s.get("ref") or f"S{n}"
        s["ref"] = ref
        db.x("""insert into research_sources (project_id, ref, tier, title, publisher, url, published_at, retrieval_provider, excerpt)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
             (project["id"], ref, s["tier"], s["title"], s.get("publisher"), s.get("url"),
              _date(s.get("published_at")), s.get("provider", "fixture"), s.get("excerpt")))
        listing.append(f"[{ref}] ({s['tier']}) {s['title']} — {s.get('publisher') or ''} {s.get('published_at') or ''}\n{s.get('excerpt') or ''}")
    user = f"Topic: {query}\nAngle: {(idea or {}).get('angle', '')}\n\nRETRIEVED SOURCES:\n" + ("\n\n".join(listing) or "(none)")
    res, _ = complete_json(out=Research, agent="research", provider=prov, model=model, system=prompts.RESEARCH, user=user,
                           user_id=job["user_id"], project_id=project["id"], idem=f"project:{project['id']}:research",
                           fixture=_fx(fx, "research"))
    valid_refs = {s["ref"] for s in retrieved}
    for c in res.claims:
        bad = [r for r in c.source_refs if r not in valid_refs]
        if bad:
            raise BusinessRuleViolation(f"Research Agent cited sources that were not retrieved: {bad}")
    db.x("delete from research_claims where project_id=%s", (project["id"],))
    for c in res.claims:
        db.x("insert into research_claims (project_id, scene_idx, claim, status, source_refs, note) values (%s,%s,%s,%s,%s,%s)",
             (project["id"], c.scene_idx, c.claim, c.status, c.source_refs, c.note))
    return res


def _date(s: str | None):
    if not s:
        return None
    s = str(s)
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else None


# ------------------------------------------------------------------ Production Pack (Research -> Script -> Visual -> SEO)
def production_pack(job: dict) -> dict:
    project = db.q1("select * from projects where id=%s", (job["project_id"],))
    tier = project["tier"]
    prov, model, fx = llm_choice({"llm_provider": project["llm_provider"], "llm_model": project["llm_model"]}, tier)
    ledger.ensure_within_cap(job["user_id"], pricing.estimate("production_pack", tier, project["target_minutes"])["total_usd"],
                             "Production Pack")
    n_scenes = job["payload"].get("scenes", 7)

    res = research(project, job, prov, model, fx)

    claims_txt = "\n".join(f"{i}. [{c.status}] {c.claim} {c.source_refs}" for i, c in enumerate(res.claims))
    user = (f"Title: {project['title']}\nTarget length: {project['target_minutes']} minutes\nScenes: {n_scenes}\n"
            f"Research summary: {res.summary}\nTimeline: {json.dumps(res.timeline)}\nClaims:\n{claims_txt}\n"
            f"Requested chapters (optional): {json.dumps(job['payload'].get('chapters') or [])}")
    draft, _ = complete_json(out=ScriptDraft, agent="script", provider=prov, model=model, system=prompts.SCRIPT_DRAFT,
                             user=user, user_id=job["user_id"], project_id=project["id"],
                             idem=f"project:{project['id']}:script_draft", fixture=_fx(fx, "script_draft"))
    if len(draft.scenes) != n_scenes:
        raise BusinessRuleViolation(f"Script Agent returned {len(draft.scenes)} scenes, expected {n_scenes}")
    for c in draft.scenes:
        if any(r >= len(res.claims) for r in c.claim_refs):
            raise BusinessRuleViolation(f"scene {c.idx} references a claim that does not exist")

    db.x("delete from scenes where project_id=%s", (project["id"],))
    for s in draft.scenes:
        db.x("""insert into scenes (project_id, idx, title, chapter_title, draft, on_screen_text)
                values (%s,%s,%s,%s,%s,%s)""", (project["id"], s.idx, s.title, s.chapter_title, s.draft, s.on_screen_text))

    scenes_txt = "\n".join(f"Scene {s.idx}: {s.title}\n{s.draft}" for s in draft.scenes)
    plan, _ = complete_json(out=FootagePlan, agent="visual_director", provider=prov, model=model,
                            system=prompts.VISUAL_DIRECTOR, user=f"Topic: {project['title']}\n\n{scenes_txt}",
                            user_id=job["user_id"], project_id=project["id"],
                            idem=f"project:{project['id']}:visual_director", fixture=_fx(fx, "visual_director"))
    if {n.scene_idx for n in plan.needs} != set(range(n_scenes)):
        raise BusinessRuleViolation("Visual Director must plan visuals for every scene")
    db.x("delete from footage_plan where project_id=%s", (project["id"],))
    for n in sorted(plan.needs, key=lambda n: (n.scene_idx, n.priority)):
        db.x("""insert into footage_plan (project_id, scene_idx, priority, tier, description, search_query, source_hint)
                values (%s,%s,%s,%s,%s,%s,%s)""",
             (project["id"], n.scene_idx, n.priority, n.tier, n.description, n.search_query, n.source_hint))
    db.x("delete from redaction_plan where project_id=%s and origin='auto' and asset_id is null", (project["id"],))
    for r in plan.redactions:
        db.x("insert into redaction_plan (project_id, scene_idx, kind, origin, t_start, t_end, reason) values (%s,%s,%s,'auto',0,0,%s)",
             (project["id"], r.scene_idx, r.kind, r.reason))

    # Draft chapters from draft word counts; exact timestamps are recomputed from audio at render time.
    chapters = draft_chapters(draft, project["target_minutes"])
    seo = seo_pack(project, job, prov, model, fx, narration="\n\n".join(s.draft for s in draft.scenes), chapters=chapters,
                   idem=f"project:{project['id']}:seo_draft")
    pack = {"research": res.model_dump(), "script": draft.model_dump(), "footage": plan.model_dump(),
            "seo": seo.model_dump(), "chapters": chapters}
    db.x("""update projects set production_pack=%s, status='pack_ready', selected_title=%s, description=%s, tags=%s,
            disclosure_line=%s where id=%s""",
         (db.jsonb(pack), seo.titles[0], seo.description, seo.tags, seo.disclosure_line, project["id"]))
    db.x("update ideas set status='in_production' where id=%s", (project["idea_id"],))
    return {"scenes": n_scenes, "claims": len(res.claims), "footage_needs": len(plan.needs)}


def fmt_ts(sec: float) -> str:
    sec = int(round(sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def draft_chapters(draft: ScriptDraft, minutes: int) -> list[dict]:
    total = minutes * 60
    n = len(draft.scenes)
    return [{"t": fmt_ts(i * total / n), "title": s.chapter_title} for i, s in enumerate(draft.scenes)]


def seo_pack(project, job, prov, model, fx, narration: str, chapters: list[dict], idem: str) -> SeoPack:
    ch_txt = "\n".join(f"{c['t']} {c['title']}" for c in chapters)
    user = f"Working title: {project['title']}\nChapters:\n{ch_txt}\n\nNarration:\n{narration[:60000]}"
    seo, _ = complete_json(out=SeoPack, agent="editor_seo", provider=prov, model=model, system=prompts.EDITOR_SEO, user=user,
                           user_id=job["user_id"], project_id=project["id"], idem=idem, fixture=_fx(fx, "seo"))
    # Enforce: chapters must be in the description exactly, starting at 0:00.
    lines = seo.description.splitlines()
    if not any(l.startswith("0:00 ") for l in lines):
        seo.description = seo.description.replace("SOURCES_AND_CREDITS", ch_txt + "\n\nSOURCES_AND_CREDITS")
    if seo.disclosure_line not in seo.description:
        seo.description += "\n\n" + seo.disclosure_line
    return seo


# ------------------------------------------------------------------ Script Agent (whole-video mode)
def expand_scene(project: dict, scene: dict, target_words: int, claims: list[dict], prov: str, model: str,
                 fx: str | None, user_id: str, render_id: str) -> str:
    claim_txt = "\n".join(f"[{c['status']}] {c['claim']}" for c in claims)
    user = (f"Video: {project['title']}\nScene {scene['idx']}: {scene['title']}\nDraft:\n{scene['draft']}\n\n"
            f"Target words: {target_words}\nResearch claims:\n{claim_txt}\nReturn idx={scene['idx']}.")
    out, _ = complete_json(out=SceneNarration, agent="script", provider=prov, model=model, system=prompts.SCRIPT_EXPAND,
                           user=user, user_id=user_id, project_id=project["id"], render_id=render_id,
                           idem=f"project:{project['id']}:expand:{scene['idx']}:{_h(scene['draft'])}",
                           fixture=_fx(fx, f"narration/scene-{scene['idx']}"), stage="script")
    if out.idx != scene["idx"]:
        raise BusinessRuleViolation("expanded narration is for the wrong scene")
    w = out.words()
    if not (0.8 * target_words <= w <= 1.25 * target_words):
        raise BusinessRuleViolation(f"scene {scene['idx']} narration has {w} words, target {target_words}")
    return out.narration


def _h(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()[:12]
