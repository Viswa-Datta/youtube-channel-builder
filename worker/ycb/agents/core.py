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
    # A tier whose provider has no key falls back to the free provider that is configured, instead of failing.
    from ..config import settings as _s
    if prov != "fixture" and _s.missing_for(prov):
        for alt in ("gemini", "openai", "anthropic"):
            alt_model = _s_model(alt)
            if alt != prov and alt_model and not _s.missing_for(alt):
                prov, model = alt, alt_model
                break
    return prov, model, (model if prov == "fixture" else None)


def _s_model(provider: str) -> str:
    """The configured default model for a provider (from the free tier entry in pricing.json)."""
    for t in pricing.P["tiers"].values():
        if t["llm"].startswith(provider + ":"):
            return t["llm"].split(":", 1)[1]
    return ""


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
MIN_IDEA_RANK = 6.0  # ideas below this are not shown


def ideas(job: dict) -> dict:
    p = job["payload"]
    ch = db.q1("select * from channels where id=%s", (p["channel_id"],))
    kit = db.q1("select * from channel_kits where channel_id=%s order by version desc limit 1", (ch["id"],))
    tier = p.get("tier", "standard")
    prov, model, fx = llm_choice(p, tier)
    ledger.ensure_within_cap(job["user_id"], pricing.estimate("ideas", tier)["total_usd"], "Ideas Agent")
    # Every subject this channel has ever been offered (picked, published or dismissed): never suggest them again.
    existing = [r["c"] for r in db.q("""select distinct case_info->>'case_name' c from ideas
                                        where channel_id=%s and case_info ? 'case_name'""", (ch["id"],)) if r["c"]]
    from .niches import preset_for
    pre = preset_for(ch["niche"], ch["sub_niche"])
    user = (f"Channel: {json.dumps((kit or {}).get('selected') or {})}\nNiche: {ch['niche']} / {ch['sub_niche'] or ''}\n"
            f"Subjects for this niche: {pre['subjects']}.\nGuidance: {pre['ideas']}\nSafety: {pre['safety']}\n"
            f"Audience: {ch['audience'] or '-'}\nTarget length: {ch['target_minutes']} min\nCount: {p.get('count', 25)}\n"
            f"Already covered (never suggest these subjects again): {json.dumps(existing[:400])}")
    est = pricing.estimate("full", tier, minutes=ch["target_minutes"])["total_usd"]
    n_batch = db.q1("select coalesce(max(batch),0)+1 b from ideas where channel_id=%s", (ch["id"],))["b"]
    want = int(p.get("count", 25))
    from ..config import settings as _settings
    ai_scenes = _settings.gen_media != "off"

    def measure(cands: list) -> list:
        """Check subjects live (Wikipedia, real page views, free media) and replace guessed scores with measured ones."""
        probes = probe_ideas(cands, job) if not fx else {}
        out = []
        for k, i in enumerate(cands):
            pr = probes.get(k)
            if pr is not None:
                feas = pr["feasibility"]
                if ai_scenes and pr["verified"]:
                    feas = max(feas, 7)  # AI scale-model scenes + animations cover what archives lack
                i.footage_feasibility_score = feas
                if pr.get("demand") is not None:
                    i.demand_score = pr["demand"]  # real Wikipedia page views, not guessed
            out.append((i, pr))
        return out

    rows: list = []
    if fx:
        batch, _ = complete_json(out=IdeaBatch, agent="ideas", provider=prov, model=model, system=prompts.IDEAS, user=user,
                                 user_id=job["user_id"], idem=f"job:{job['id']}:ideas", fixture=_fx(fx, "ideas"))
        rows = measure(batch.ideas)
    else:
        # Rounds of ~13 ideas, each checked live; only ideas that rank MIN_IDEA_RANK or higher are kept. Repeats of
        # subjects the channel was already offered are thrown away. Stops at `want` strong ideas or 6 rounds.
        from ..schemas import IdeaPart
        taken = {e.strip().lower() for e in existing}
        proposed: list[str] = []
        weak = 0
        for part in range(6):
            good = [r for r in rows if rank_score(r[0]) >= MIN_IDEA_RANK and (r[1] is None or r[1]["verified"])]
            if len(good) >= want:
                break
            n = 13
            ask = user.replace(f"Count: {want}", f"Count: {n} (return exactly {n} ideas)")
            if proposed:
                ask += "\nAlso already proposed in this batch (different subjects only): " + json.dumps(proposed)
            if weak:
                ask += ("\nMany earlier suggestions were rejected for LOW PUBLIC INTEREST. Choose subjects that far more "
                        "people read about and search for.")
            half, _ = complete_json(out=IdeaPart, agent="ideas", provider=prov, model=model,
                                    system=prompts.IDEAS.replace("Return 20-30 ideas.", f"Return {n} ideas."),
                                    user=ask, user_id=job["user_id"], idem=f"job:{job['id']}:ideas:{part}")
            fresh = []
            for i in half.ideas:
                k = (i.case_name or i.title or "").strip().lower()
                proposed.append(i.case_name or i.title)
                if k and k not in taken:
                    taken.add(k)
                    fresh.append(i)
            measured = measure(fresh)
            weak += sum(1 for i, _ in measured if rank_score(i) < MIN_IDEA_RANK)
            rows += measured
            good = [r for r in rows if rank_score(r[0]) >= MIN_IDEA_RANK and (r[1] is None or r[1]["verified"])]
            hb_note(job, f"strong ideas found: {len(good)}/{want} (checked {len(rows)})")
        rows = [r for r in rows if rank_score(r[0]) >= MIN_IDEA_RANK and (r[1] is None or r[1]["verified"])]
    probes = {k: r[1] for k, r in enumerate(rows) if r[1] is not None}
    ranked = sorted(rows, key=lambda r: (rank_score(r[0]), bool(r[1] and r[1]["verified"])), reverse=True)[:want]
    for pos, (i, pr) in enumerate(ranked):
        case = {k: v for k, v in {"case_name": i.case_name, "location": i.location, "year": i.year, "agency": i.agency,
                                   "case_status": i.case_status, "public_footage": i.public_footage,
                                   "search_terms": i.search_terms,
                                   "reference": (pr or {}).get("reference"),
                                   "youtube_checked": (pr or {}).get("youtube_checked"),
                                   "youtube_error": (pr or {}).get("youtube_error"),
                                   "pageviews": (pr or {}).get("pageviews"),
                                   "free_videos": (pr or {}).get("free_videos"),
                                   "free_images": (pr or {}).get("free_images")}.items() if v not in (None, [], "")}
        db.x("""insert into ideas (channel_id, batch, title, hook, angle, pillar, demand_score, competition_score,
                feasibility_score, est_cost_usd, rank_score, position, rationale, case_info, footage_evidence, case_verified)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
             (ch["id"], n_batch, i.title, i.hook, i.angle, i.pillar, i.demand_score, i.competition_score,
              i.footage_feasibility_score, est, rank_score(i), pos, i.rationale, db.jsonb(case),
              db.jsonb((pr or {}).get("evidence", [])), bool(pr and pr["verified"])))
    return {"count": len(ranked), "batch": n_batch, "checked": len(probes),
            "confirmed": sum(1 for _, pr in ranked if pr and pr["verified"])}


def hb_note(job: dict, note: str) -> None:
    """Show progress on the job (the idea board's progress box reads it)."""
    try:
        db.x("update jobs set progress_note=%s where id=%s", (note, job["id"]))
    except Exception:  # noqa: BLE001
        pass


def probe_ideas(ideas: list, job: dict | None = None) -> dict[int, dict]:
    """Live-check every case-based idea (exists? official footage found?). Network trouble never fails the job."""
    from concurrent.futures import ThreadPoolExecutor
    from ..footage.discovery import probe_case
    todo = [(k, i) for k, i in enumerate(ideas) if i.case_name]

    from ..config import settings as _settings
    from ..footage.discovery import probe_subject

    def one(ki):
        k, i = ki
        try:
            if _settings.footage_mode == "assisted":
                return k, probe_case(i.case_name, i.location, i.agency, i.search_terms)
            return k, probe_subject(i.case_name, i.search_terms)
        except Exception as e:  # noqa: BLE001
            return k, {"verified": False, "reference": None, "evidence": [], "feasibility": i.footage_feasibility_score,
                       "error": str(e)[:200]}
    done: dict[int, dict] = {}
    with ThreadPoolExecutor(4) as ex:
        for k, r in ex.map(one, todo):
            done[k] = r
            if job:
                hb_note(job, f"checking subjects on Wikipedia and free archives: {len(done)}/{len(todo)}")
    return done


# ------------------------------------------------------------------ Research Agent
def _retrieve(project: dict, query: str, fixture_set: str | None, idea: dict | None = None) -> list[dict]:
    """Collect sources in tier order. Fixture mode reads a pre-verified list (URLs checked by hand)."""
    if fixture_set:
        from ..config import REPO_ROOT
        return json.loads((REPO_ROOT / "fixtures" / fixture_set / "sources.json").read_text())
    from ..footage import discovery
    found: list[dict] = []
    case = (idea or {}).get("case_info") or {}
    # Official releases the idea check already found (e.g. the agency's own bodycam upload): agency_release.
    for e in (idea or {}).get("footage_evidence") or []:
        if e.get("official"):
            found.append({"title": e["title"], "url": e["url"], "publisher": e.get("channel") or e["source"],
                          "published_at": e.get("published"), "tier": "agency_release", "provider": e["source"].lower(),
                          "excerpt": "Official release found by the footage check (video; content not transcribed)."})
    # The case's own reference articles (title must name the case), section by section: main article in full,
    # up to two related articles (e.g. the trial) more briefly.
    titles: list[str] = []
    if case.get("case_name"):
        try:
            titles = [e.title for e in discovery.case_articles(case["case_name"], discovery.wikipedia(case["case_name"], 6))]
        except Exception:  # noqa: BLE001
            titles = []
    ref = case.get("reference") or {}
    if ref.get("title") and ref["title"] in titles:
        titles.remove(ref["title"])
        titles.insert(0, ref["title"])
    for k, t in enumerate(titles[:3]):
        for sec in discovery.wikipedia_sections(t, max_chars=30000 if k == 0 else 8000, excerpt_chars=3500 if k == 0 else 1800):
            found.append({**sec, "tier": "reference", "provider": "wikipedia"})
    def safe(fn, *a, **k):  # one slow or broken source never stops the research
        try:
            return fn(*a, **k)
        except Exception:  # noqa: BLE001
            return []
    for item in safe(sources.courtlistener, query):
        found.append({**item, "tier": "court_record", "provider": "courtlistener"})
    for item in safe(sources.gdelt_news, query, gov_only=True):
        found.append({**item, "tier": "agency_release", "provider": "gdelt"})
    for item in safe(sources.gdelt_news, query):
        found.append({**item, "tier": "contemporaneous_news", "provider": "gdelt"})
    for c in safe(sources.library_of_congress, query, rows=5):
        found.append({"title": c.title, "url": c.source_url, "publisher": "Library of Congress",
                      "published_at": c.release_date, "tier": "reference", "provider": "loc", "excerpt": ""})
    # A source with no text cannot back a claim (the research agent would only see its headline): leave it out.
    found = [s for s in found if len((s.get("excerpt") or "").strip()) >= 40]
    found.sort(key=lambda s: TIER_ORDER[s["tier"]])
    return found[:60]


def research(project: dict, job: dict, prov: str, model: str, fx: str | None) -> Research:
    idea = db.q1("select * from ideas where id=%s", (project["idea_id"],)) if project["idea_id"] else None
    case = (idea or {}).get("case_info") or {}
    query = case.get("case_name") or (idea or {}).get("title") or project["title"]
    retrieved = _retrieve(project, query, fx, idea)
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
    case_line = " · ".join(str(case[k]) for k in ("case_name", "location", "year", "agency", "case_status") if case.get(k))
    n_claims = max(20, int(project["target_minutes"]))
    user = (f"Topic: {(idea or {}).get('title') or project['title']}\nCase: {case_line or '-'}\n"
            f"Angle: {(idea or {}).get('angle', '')}\nThis is a {project['target_minutes']}-minute documentary: extract "
            f"{n_claims}-{2 * n_claims} specific, dated factual claims covering the whole story in order (background, the "
            f"incident, the investigation, official statements, legal proceedings, outcomes, aftermath), and a detailed "
            f"timeline (one entry per dated event, 'YYYY-MM-DD: event').\n\nRETRIEVED SOURCES:\n")
    if fx or len(listing) <= 6:
        user = user + ("\n\n".join(listing) or "(none)")
        res, _ = complete_json(out=Research, agent="research", provider=prov, model=model, system=prompts.RESEARCH, user=user,
                               user_id=job["user_id"], project_id=project["id"], idem=f"project:{project['id']}:research",
                               fixture=_fx(fx, "research"))
    else:
        res = _research_in_batches(project, job, prov, model, user, listing)
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


def _research_in_batches(project: dict, job: dict, prov: str, model: str, header: str, listing: list[str]) -> Research:
    """Read the sources ~5 at a time so every section yields its facts (one big call summarises too much),
    then merge: claims de-duplicated in story order, timeline de-duplicated and sorted by date."""
    batches = [listing[i:i + 5] for i in range(0, len(listing), 5)]
    parts: list[Research] = []
    for b, chunk in enumerate(batches):
        ask = (header.split("RETRIEVED SOURCES:")[0] +
               f"BATCH {b + 1} of {len(batches)}: extract EVERY specific fact these sources support (aim for 8-20 claims), "
               "citing only the refs shown below. Include dated timeline entries found in these sources.\n\n"
               "RETRIEVED SOURCES:\n" + "\n\n".join(chunk))
        for attempt in range(2):
            try:
                r, _ = complete_json(out=Research, agent="research", provider=prov, model=model, system=prompts.RESEARCH,
                                     user=ask, user_id=job["user_id"], project_id=project["id"],
                                     idem=f"project:{project['id']}:research:{b}:{attempt}")
                parts.append(r)
                break
            except Exception:  # noqa: BLE001 - retry this batch once, then move on
                continue
    if not parts:
        raise BusinessRuleViolation("Research Agent failed on every batch of sources")
    seen, claims = set(), []
    for r in parts:
        for c in r.claims:
            k = re.sub(r"\W+", " ", c.claim.lower()).strip()
            if k not in seen:
                seen.add(k)
                claims.append(c)
    tl = list(dict.fromkeys(t for r in parts for t in r.timeline))
    tl.sort(key=lambda t: (t[:10] if re.match(r"\d{4}-\d{2}-\d{2}", t) else "9999"))
    uniq = lambda xs: list(dict.fromkeys(xs))  # noqa: E731
    return Research(summary=parts[0].summary, timeline=tl, claims=claims[:150],
                    disclaimers=uniq(d for r in parts for d in r.disclaimers),
                    sensitive_topics=uniq(t for r in parts for t in r.sensitive_topics),
                    fact_check_notes=uniq(n for r in parts for n in r.fact_check_notes)[:30],
                    gaps=uniq(g for r in parts for g in r.gaps)[:30])


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
    n_scenes = job["payload"].get("scenes") or scenes_for(project["target_minutes"])

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
    for c in draft.scenes:  # a pointer to a claim that does not exist is dropped (the claim list is the source of truth)
        c.claim_refs = [r for r in c.claim_refs if 0 <= r < len(res.claims)]
        if c.chart and not chart_numbers_sourced(c.chart, [x.claim for x in res.claims]):
            c.chart = None  # a number we cannot find in the research never reaches the screen

    db.x("delete from scenes where project_id=%s", (project["id"],))
    for s in draft.scenes:
        db.x("""insert into scenes (project_id, idx, title, chapter_title, draft, on_screen_text)
                values (%s,%s,%s,%s,%s,%s)""", (project["id"], s.idx, s.title, s.chapter_title, s.draft, s.on_screen_text))

    plan = visual_plan(project, job, draft, prov, model, fx)
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


def _nums(text: str) -> set[float]:
    out = set()
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            out.add(round(float(m.group().replace(",", "")), 4))
        except ValueError:
            pass
    return out


def chart_numbers_sourced(chart, claims: list[str]) -> bool:
    """Every value in the chart must literally appear in a research claim."""
    pool = set().union(*(_nums(c) for c in claims)) if claims else set()
    return all(round(float(v), 4) in pool for v in chart.values)


def visual_plan(project: dict, job: dict, draft, prov: str, model: str, fx: str | None):
    """Visual Director in groups of 4 scenes (long videos overwhelm one call). Scenes a call skips are asked for
    again once; if still missing they get an honest documents/maps plan, never invented footage."""
    from ..schemas import FootageNeed
    if fx:  # fixture mode: one call, as recorded
        plan, _ = complete_json(out=FootagePlan, agent="visual_director", provider=prov, model=model, system=prompts.VISUAL_DIRECTOR,
                                user=f"Topic: {project['title']}\n\n" + "\n".join(f"Scene {s.idx}: {s.title}\n{s.draft}" for s in draft.scenes),
                                user_id=job["user_id"], project_id=project["id"], idem=f"project:{project['id']}:visual_director",
                                fixture=_fx(fx, "visual_director"))
        if {n.scene_idx for n in plan.needs} != {s.idx for s in draft.scenes}:
            raise BusinessRuleViolation("Visual Director must plan visuals for every scene")
        return plan
    idea = db.q1("select case_info from ideas where id=%s", (project["idea_id"],)) if project["idea_id"] else None
    case = (idea or {}).get("case_info") or {}
    case_line = " · ".join(str(case[k]) for k in ("case_name", "location", "year", "agency") if case.get(k))
    from .niches import preset_for
    ch = db.q1("select niche, sub_niche from channels where id=%s", (project["channel_id"],)) or {}
    pre = preset_for(ch.get("niche"), ch.get("sub_niche"))
    needs, reds, foia = [], [], []
    groups = [draft.scenes[i:i + 4] for i in range(0, len(draft.scenes), 4)]
    for g in groups:
        want = {s.idx for s in g}
        got: set[int] = set()
        for attempt in range(2):
            todo = [s for s in g if s.idx not in got]
            txt = "\n".join(f"Scene {s.idx}: {s.title}\n{s.draft}" for s in todo)
            ask = (f"Topic: {project['title']}\nSubject: {case_line or '-'}\nNiche visual guidance: {pre['visuals']}\n"
                   f"Plan visuals for EXACTLY these scene numbers: {[s.idx for s in todo]} (every one of them, using these "
                   f"scene_idx values).\n\n{txt}")
            try:
                part, _ = complete_json(out=FootagePlan, agent="visual_director", provider=prov, model=model,
                                        system=prompts.VISUAL_DIRECTOR, user=ask, user_id=job["user_id"], project_id=project["id"],
                                        idem=f"project:{project['id']}:visual_director:{min(want)}:{attempt}")
            except Exception:  # noqa: BLE001 - schema failure on one group: retry it, then fall back
                continue
            for n in part.needs:
                if n.scene_idx in want:
                    needs.append(n)
                    got.add(n.scene_idx)
            reds += [r for r in part.redactions if r.scene_idx in want]
            foia += part.foia_suggestions
            if got == want:
                break
        for s in g:
            if s.idx not in got:  # honest fallback: records, maps and documents for this scene
                base = case.get("case_name") or project["title"]
                needs.append(FootageNeed(scene_idx=s.idx, priority=2, tier="news_document",
                                         description=f"Documents, records or location photos for: {s.title}",
                                         search_query=f"{base} {s.title}"[:120],
                                         source_hint="court records, agency press releases, Wikimedia Commons"))
    return FootagePlan(needs=needs, redactions=reds, foia_suggestions=list(dict.fromkeys(foia))[:10])


def scenes_for(minutes: int) -> int:
    """Scene count by length: long videos get more, shorter scenes (each ~4 minutes at 45-60 min)."""
    return {8: 5, 12: 7, 20: 8, 30: 10, 45: 12, 60: 15}.get(int(minutes), max(5, min(16, int(minutes) // 4)))


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
                           idem=f"project:{project['id']}:expand:{scene['idx']}:{_h(scene['draft'])}:{target_words}",
                           fixture=_fx(fx, f"narration/scene-{scene['idx']}"), stage="script")
    if out.idx != scene["idx"]:
        raise BusinessRuleViolation("expanded narration is for the wrong scene")
    text = out.narration
    w = len(text.split())
    # Models often write short. Ask for a continuation (new facts from the claims, no repetition) until long enough.
    for k in range(max(3, -(-target_words // 450))):  # long scenes (e.g. a short video re-made at 60 min) need more parts
        if w >= 0.9 * target_words or fx:
            break
        more = min(900, target_words - w)
        cont_user = (f"Video: {project['title']}\nScene {scene['idx']}: {scene['title']}\n\nNARRATION SO FAR ({w} words):\n{text}\n\n"
                     f"Write the NEXT part of this scene's narration: about {more} words that continue naturally from the last "
                     f"sentence. Add detail and context from the research claims that the narration so far has not used; never "
                     f"repeat a point already made and do not start the story again. Target words: {more}.\n"
                     f"Research claims:\n{claim_txt}\nReturn idx={scene['idx']}.")
        add, _ = complete_json(out=SceneNarration, agent="script", provider=prov, model=model, system=prompts.SCRIPT_EXPAND,
                               user=cont_user, user_id=user_id, project_id=project["id"], render_id=render_id,
                               idem=f"project:{project['id']}:expand:{scene['idx']}:{_h(scene['draft'])}:{target_words}:more{k}", stage="script")
        text = text.rstrip() + "\n\n" + add.narration.strip()
        w = len(text.split())
    if not (0.8 * target_words <= w <= 1.5 * target_words) and not fx:
        raise BusinessRuleViolation(f"scene {scene['idx']} narration has {w} words, target {target_words}")
    if fx and not (0.8 * target_words <= w <= 1.25 * target_words):
        raise BusinessRuleViolation(f"scene {scene['idx']} narration has {w} words, target {target_words}")
    return text


def _h(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()[:12]


# ------------------------------------------------------------------ Motion Graphics Agent
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", (s or "").lower())).strip()


def verify_graphics(graphics: list, narration: str, claims: list[str], geocode=None) -> list[dict]:
    """Keep only animations the app can stand behind: numbers that literally appear in the narration or research,
    dates that appear in the text, quotes copied from the narration, places that geocode. Distances are computed."""
    from ..media import maps
    geocode = geocode or maps.geocode
    pool = _nums(narration).union(*(_nums(c) for c in claims)) if claims else _nums(narration)
    text = _norm(narration + " " + " ".join(claims))
    nar = _norm(narration)
    out, used = [], {"points": 0, "quote": 0}
    for g in graphics:
        g = g.model_dump() if hasattr(g, "model_dump") else dict(g)
        k = (g.get("kind") or "").strip().lower()
        try:
            if k == "counter":
                if g.get("value") is None or round(float(g["value"]), 4) not in pool or not g.get("title"):
                    continue
            elif k == "bars":
                lv = list(zip(g.get("labels") or [], g.get("values") or []))
                if not 2 <= len(lv) <= 6 or any(round(float(v), 4) not in pool for _, v in lv):
                    continue
                g["labels"], g["values"] = [str(a)[:26] for a, _ in lv], [float(v) for _, v in lv]
            elif k == "timeline":
                items = []
                for it in g.get("items") or []:
                    date, _, ev = str(it).partition(":")
                    yrs = re.findall(r"\d{3,4}", date)
                    if ev.strip() and yrs and all(y in text for y in yrs):
                        items.append(f"{date.strip()[:14]}: {ev.strip()[:70]}")
                if len(items) < 3:
                    continue
                g["items"] = items[:6]
            elif k == "points":
                items = [" ".join(str(x).split()[:9]) for x in (g.get("items") or []) if str(x).strip()][:5]
                if len(items) < 2 or used["points"] >= 1:
                    continue
                if any(n not in pool for it in items for n in _nums(it)):
                    continue
                g["items"] = items
                used["points"] += 1
            elif k == "quote":
                q = (g.get("text") or "").strip()
                if not (8 <= len(q.split()) <= 34) or _norm(q) not in nar or used["quote"] >= 1:
                    continue
                used["quote"] += 1
            elif k == "place":
                hit = geocode(g.get("place") or "")
                if not hit:
                    continue
                g["lat"], g["lon"] = hit[0], hit[1]
                g["place_label"] = (g.get("place_label") or g["place"].split(",")[0])[:34]
            elif k == "route":
                a, b = geocode(g.get("from_place") or ""), geocode(g.get("to_place") or "")
                if not a or not b or (abs(a[0] - b[0]) < 1e-3 and abs(a[1] - b[1]) < 1e-3):
                    continue
                g["a"] = [a[0], a[1], (g["from_place"].split(",")[0])[:26]]
                g["b"] = [b[0], b[1], (g["to_place"].split(",")[0])[:26]]
            else:
                continue
        except (TypeError, ValueError):
            continue
        g["kind"] = k
        out.append(g)
    return out


def plan_graphics(project: dict, scene: dict, claims: list[str], prov: str, model: str, fx: str | None,
                  user_id: str, render_id: str | None) -> list[dict]:
    """One LLM call per scene; every result is verified before it can reach the screen."""
    from ..schemas import SceneGraphics
    if fx:
        return []  # fixture runs have no recorded motion plan
    words = len(scene["narration"].split())
    want = max(2, min(12, round(words / 80)))  # ~1 animation per 30 s of narration
    user = (f"Video: {project['title']}\nScene {scene['idx']}: {scene['title']}\nPlan about {want} animations.\n\n"
            f"NARRATION:\n{scene['narration']}\n\nRESEARCH CLAIMS:\n" + "\n".join(claims[:80]) + f"\n\nReturn idx={scene['idx']}.")
    try:
        out, _ = complete_json(out=SceneGraphics, agent="motion_graphics", provider=prov, model=model,
                               system=prompts.MOTION_GRAPHICS, user=user, user_id=user_id, project_id=project["id"],
                               render_id=render_id, idem=f"project:{project['id']}:graphics:{scene['idx']}:{_h(scene['narration'])}",
                               stage="script")
    except Exception:  # noqa: BLE001 - no animations is better than a failed video
        return []
    return verify_graphics(out.graphics, scene["narration"], claims)


# ------------------------------------------------------------------ Shot List Agent
def make_beats(sentences: list[dict], min_len: float = 4.5, max_len: float = 11.0) -> list[dict]:
    """Group spoken sentences into beats of ~5-10 s (short sentences merged, very long ones split in two)."""
    beats: list[dict] = []
    cur = None
    for x in sentences:
        st, en, tx = float(x["start"]), float(x["end"]), x["text"]
        if en - st > max_len:
            if cur:
                beats.append(cur)
                cur = None
            mid = (st + en) / 2
            beats.append({"start": st, "end": mid, "text": tx})
            beats.append({"start": mid, "end": en, "text": tx + " (continued)"})
            continue
        if cur is None:
            cur = {"start": st, "end": en, "text": tx}
        else:
            cur["end"], cur["text"] = en, cur["text"] + " " + tx
        if cur["end"] - cur["start"] >= min_len:
            beats.append(cur)
            cur = None
    if cur:
        if beats and cur["end"] - cur["start"] < 2.5:
            beats[-1]["end"], beats[-1]["text"] = cur["end"], beats[-1]["text"] + " " + cur["text"]
        else:
            beats.append(cur)
    for i, b in enumerate(beats):
        b["beat"] = i
    return beats


def plan_shots(project: dict, scene: dict, beats: list[dict], items: list[dict], prov: str, model: str,
               fx: str | None, user_id: str, render_id: str | None, gen_images: int = 0, gen_videos: int = 0):
    """items: [{'id': 'A1', 'kind': 'photo'|'clip', 'title': ...}]. Returns ShotPlan or None (fixture runs, failures)."""
    from ..schemas import ShotPlan
    if fx or not beats:
        return None
    listing = "\n".join(f"{it['id']} [{it['kind']}] {it['title'][:110]}" for it in items) or "(none yet)"
    beat_txt = "\n".join(f"{b['beat']}: ({b['end'] - b['start']:.0f}s) {b['text']}" for b in beats)
    user = (f"Video: {project['title']}\nScene {scene['idx']}: {scene['title']}\n\nBEATS:\n{beat_txt}\n\n"
            f"AVAILABLE PHOTOS/CLIPS:\n{listing}\n\n"
            + (f"GENERATION: allowed, at most {gen_images} image(s) and {gen_videos} video(s) in this scene.\n\n"
               if gen_images or gen_videos else "GENERATION: not allowed (leave generate empty).\n\n")
            + f"Return idx={scene['idx']} with {len(beats)} shots.")
    try:
        out, _ = complete_json(out=ShotPlan, agent="shot_list", provider=prov, model=model, system=prompts.SHOT_LIST,
                               user=user, user_id=user_id, project_id=project["id"], render_id=render_id,
                               idem=f"project:{project['id']}:shots:{scene['idx']}:{_h(beat_txt + listing)}:{gen_images}:{gen_videos}", stage="footage")
        return out
    except Exception:  # noqa: BLE001 - without a plan the editor falls back to rotating the scene's media
        return None


# ------------------------------------------------------------------ Shorts Agent
def shorts_scripts(project: dict, script: str, claims: list[str], media: list[dict], n: int, gen_per_short: int,
                   prov: str, model: str, user_id: str, render_id: str | None, avoid: list[str] | None = None) -> list:
    """Write n vertical Shorts from the documentary's own research and script. media: [{id, kind, title}]."""
    from ..schemas import ShortsBatch
    listing = "\n".join(f"{m['id']} ({m['kind']}): {m['title'][:110]}" for m in media[:120]) or "(none)"
    user = (f"Documentary: {project.get('selected_title') or project['title']}\n"
            + (f"Shorts already made (tell different stories): {'; '.join(avoid)}\n" if avoid else "")
            + f"\nMEDIA:\n{listing}\n\nRESEARCH CLAIMS:\n" + "\n".join(claims[:120])
            + f"\n\nSCRIPT:\n{script[:24000]}")
    out, _ = complete_json(out=ShortsBatch, agent="shorts", provider=prov, model=model,
                           system=prompts.SHORTS.format(n=n, gen=gen_per_short), user=user, user_id=user_id,
                           project_id=project["id"], render_id=render_id,
                           idem=f"project:{project['id']}:shorts:{n}:{_h(script)}:{_h(' '.join(avoid or []))}",
                           stage="shorts")
    ids = {m["id"] for m in media}
    for s in out.shorts:
        seen, n_gen = set(), 0
        for ln in s.lines:
            if ln.asset not in ids or ln.asset in seen:
                ln.asset = ""
            seen.add(ln.asset)
            if ln.asset or n_gen >= gen_per_short:
                ln.generate = ""
            n_gen += bool(ln.generate)
    return out.shorts[:n]
