"""End-to-end acceptance run for the 30-minute True Crime test video ("The Quiet Case File").

Runs the real worker as a subprocess against a real Postgres. LLM steps use the fixture provider (no key in
this environment); voice (Kokoro), footage search, blur, FFmpeg assembly, ledger and queue are all real.
Kills the worker with SIGKILL mid-render and checks the restart resumes without corrupting the project.

    python tests/e2e_acceptance.py  -> writes reports/e2e-30min.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import harness as H  # noqa: E402

REPORT = H.REPO / "reports"
REPORT.mkdir(exist_ok=True)
STORAGE = H.REPO / ".storage-e2e"
WORK = Path("/tmp/ycb-e2e-work")
results: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": {}}


def step(name, ok, **detail):
    results["steps"][name] = {"pass": bool(ok), **detail}
    print(("PASS " if ok else "FAIL ") + name, json.dumps(detail, default=str)[:400], flush=True)
    (REPORT / "e2e-30min.json").write_text(json.dumps(results, indent=1, default=str))


def main():
    import shutil
    shutil.rmtree(STORAGE, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    url = H.fresh_db("ycb_e2e")
    db = H.connect(url)
    env = H.worker_env(url, LOCAL_STORAGE_DIR=STORAGE, WORK_DIR=WORK, JOB_LEASE_SECONDS=20, JOB_POLL_SECONDS=1)
    os.environ.update(env)
    sys.path.insert(0, str(H.REPO / "worker"))
    from ycb import pricing  # noqa: E402  (after env so settings pick it up)
    from ycb.stages import pipeline  # noqa: E402

    log = REPORT / "e2e-worker.log"
    log.write_text("")
    w1 = H.Worker(env, log, "worker-A").start()

    # 1. sign in: Supabase Auth creates auth.users; trigger creates the profile row
    uid = db.execute("insert into auth.users (email, raw_user_meta_data) values ('test@example.com','{\"full_name\":\"Test User\"}') returning id").fetchone()["id"]
    prof = db.execute("select * from users where id=%s", (uid,)).fetchone()
    step("auth_profile_created", prof is not None, email=prof["email"], cap=float(prof["monthly_cap_usd"]))

    # 2. niche intake
    ch = db.execute("""insert into channels (user_id, niche, sub_niche, audience, language, tone, target_minutes, faceless, competitor_urls)
                       values (%s,'True Crime Documentary','Cold cases and investigations','Adults 25-55 who like long-form documentaries',
                       'en','calm, serious, long-form documentary narration',30,true,
                       array['Dr Insanity (style reference only)','Midwest Safety (style reference only)']) returning id""", (uid,)).fetchone()["id"]
    fx = {"llm_provider": "fixture", "llm_model": "quiet-case-file", "tier": "free"}

    def run_job(kind, payload, project_id=None, timeout=120):
        j = db.execute("insert into jobs (user_id, project_id, kind, payload) values (%s,%s,%s,%s) returning id",
                       (uid, project_id, kind, json.dumps(payload))).fetchone()["id"]
        return H.wait_for(lambda: (r := db.execute("select status, last_error from jobs where id=%s", (j,)).fetchone())["status"]
                          in ("succeeded", "failed") and r, timeout, 1, kind)

    est_kit = pricing.estimate("channel_kit", "free")
    r = run_job("channel_kit", {"channel_id": str(ch), **fx})
    kit = db.execute("select * from channel_kits where channel_id=%s", (ch,)).fetchone()
    step("channel_kit", r["status"] == "succeeded" and len(kit["data"]["names"]) == 5,
         names=[n["name"] for n in kit["data"]["names"]], pillars=kit["data"]["pillars"], estimate_usd=est_kit["total_usd"])

    r = run_job("ideas", {"channel_id": str(ch), "count": 25, **fx})
    ideas = db.execute("select * from ideas where channel_id=%s order by rank_score desc", (ch,)).fetchall()
    step("ideas_20_plus_ranked", r["status"] == "succeeded" and len(ideas) >= 20,
         count=len(ideas), top3=[(i["title"], float(i["rank_score"])) for i in ideas[:3]])

    # 3. user picks Idea 1 (human gate) -> project
    idea = ideas[0]
    db.execute("update ideas set status='shortlisted' where id=%s", (idea["id"],))
    pid = db.execute("""insert into projects (user_id, channel_id, idea_id, title, tier, target_minutes, llm_provider, llm_model,
                        voice_provider, voice_id, voice_speed, caption_mode)
                        values (%s,%s,%s,%s,'free',30,'fixture','quiet-case-file','kokoro','am_michael',0.95,'both') returning id""",
                     (uid, ch, idea["id"], idea["title"])).fetchone()["id"]
    est_full_free = pricing.estimate("full", "free", 30)
    est_full_std = pricing.estimate("full", "standard", 30)
    db.execute("insert into estimates (user_id, project_id, action, tier, lines, total_usd, accepted) values (%s,%s,'full','free',%s,%s,true)",
               (uid, pid, json.dumps(est_full_free["lines"]), est_full_free["total_usd"]))
    step("cost_estimate_shown_before_generation", est_full_free["total_usd"] == 0 and est_full_std["total_usd"] > 0,
         free_total=est_full_free["total_usd"], standard_total=est_full_std["total_usd"],
         premium_total=pricing.estimate("full", "premium", 30)["total_usd"], math=est_full_free["math"])

    r = run_job("production_pack", {"scenes": 7}, project_id=pid)
    p = db.execute("select * from projects where id=%s", (pid,)).fetchone()
    scenes = db.execute("select idx, chapter_title, draft from scenes where project_id=%s order by idx", (pid,)).fetchall()
    claims = db.execute("select status, count(*) n from research_claims where project_id=%s group by 1", (pid,)).fetchall()
    srcs = db.execute("select tier, count(*) n from research_sources where project_id=%s group by 1", (pid,)).fetchall()
    plan = db.execute("select tier, count(*) n from footage_plan where project_id=%s group by 1", (pid,)).fetchall()
    step("production_pack", r["status"] == "succeeded" and len(scenes) == 7 and p["status"] == "pack_ready",
         error=r["last_error"], chapters=[s["chapter_title"] for s in scenes], claims={c["status"]: c["n"] for c in claims},
         sources={s["tier"]: s["n"] for s in srcs}, footage_plan={x["tier"]: x["n"] for x in plan},
         titles=p["production_pack"]["seo"]["titles"] if p["production_pack"] else None)

    # 4. whole video render, with a crash in the middle
    v = db.execute("select coalesce(max(version),0)+1 v from renders where project_id=%s", (pid,)).fetchone()["v"]
    rid = db.execute("insert into renders (project_id, version, estimate_usd) values (%s,%s,%s) returning id",
                     (pid, v, pricing.estimate("whole_video", "free", 30)["total_usd"])).fetchone()["id"]
    db.execute("update projects set status='rendering' where id=%s", (pid,))
    db.execute("""insert into jobs (user_id, project_id, render_id, kind, stage, idempotency_key, priority)
                  values (%s,%s,%s,'render_stage','script',%s,50)""", (uid, pid, rid, f"render:{rid}:script"))
    t_render = time.time()
    voiced = H.wait_for(lambda: (n := db.execute("select count(*) n from scenes where project_id=%s and audio_hash is not null", (pid,)).fetchone()["n"]) >= 2 and n,
                        3600, 5, "two voiced scenes")
    w1.kill()
    killed_at = db.execute("select stage, progress from renders where id=%s", (rid,)).fetchone()
    before = {r_["idx"]: r_["audio_hash"] for r_ in db.execute("select idx, audio_hash from scenes where project_id=%s", (pid,)).fetchall()}
    ledger_before = db.execute("select count(*) n from cost_ledger where stage='voice' and project_id=%s", (pid,)).fetchone()["n"]
    step("worker_killed_mid_render", True, scenes_voiced_before_kill=voiced, render_state=dict(killed_at))
    w2 = H.Worker(env, log, "worker-B").start()
    done = H.wait_for(lambda: (r_ := db.execute("select * from renders where id=%s", (rid,)).fetchone())["status"] in ("succeeded", "failed") and r_,
                      4 * 3600, 10, "render to finish")
    render_minutes = round((time.time() - t_render) / 60, 1)
    jobs = db.execute("select stage, status, attempts, locked_by, last_error from jobs where render_id=%s order by created_at", (rid,)).fetchall()
    voice_rows = db.execute("select idempotency_key, count(*) n from cost_ledger where stage='voice' and project_id=%s group by 1", (pid,)).fetchall()
    after = {r_["idx"]: r_["audio_hash"] for r_ in db.execute("select idx, audio_hash from scenes where project_id=%s", (pid,)).fetchall()}
    resumed_ok = all(after[k] == h for k, h in before.items() if h)
    step("retry_resumes_after_kill", done["status"] == "succeeded" and resumed_ok and all(n["n"] == 1 for n in voice_rows),
         render_status=done["status"], error=done["error"], render_minutes=render_minutes,
         jobs=[dict(j) for j in jobs], voice_ledger_rows=len(voice_rows), voice_rows_before_kill=ledger_before,
         no_double_charge=all(n["n"] == 1 for n in voice_rows), voiced_scenes_not_redone=resumed_ok)
    w2.stop()
    if done["status"] != "succeeded":
        return

    # 5. export acceptance (ffprobe etc.)
    from ycb.media.ff import acceptance, probe
    mp4 = STORAGE / done["mp4_path"]
    acc = acceptance(mp4)
    info = probe(mp4)
    srt = (STORAGE / done["srt_path"]).read_text()
    n_cues = srt.count(" --> ")
    words = db.execute("select sum(word_count) w from scenes where project_id=%s", (pid,)).fetchone()["w"]
    step("export_has_voice_audio", acc["has_audio"] and acc.get("silence_ratio", 1) < 0.2, **acc)
    step("loudness_minus_14_lufs", acc.get("integrated_lufs") is not None and abs(acc["integrated_lufs"] + 14) <= 1.0,
         integrated_lufs=acc.get("integrated_lufs"), true_peak=acc.get("true_peak_dbfs"))
    step("full_30_min_narration", 26 * 60 <= acc["duration"] <= 34 * 60 and 4100 <= words <= 4500,
         duration_min=round(acc["duration"] / 60, 2), words=int(words))
    step("chapters_and_captions", acc["chapters"] == 7 and n_cues > 200 and acc["subtitle_streams"] == 1,
         chapters=[(round(float(c["start_time"]), 1), c["tags"].get("title")) for c in info["chapters"]], srt_cues=n_cues)
    for k in ("script_path", "pack_path", "thumb_path", "srt_path", "mp4_path"):
        assert (STORAGE / done[k]).exists(), k
    pack = (STORAGE / done["pack_path"]).read_text()
    step("downloads_present", all((STORAGE / done[k]).exists() for k in ("script_path", "pack_path", "thumb_path", "srt_path", "mp4_path")),
         files={k: done[k] for k in ("mp4_path", "srt_path", "script_path", "pack_path", "thumb_path")},
         pack_has_chapters="0:00 Cold open" in pack, pack_has_credits="Sources and footage credits" in pack)

    # 6. cost: estimate vs ledger
    led = db.execute("select provider, model, count(*) n, sum(cost_usd) c from cost_ledger where project_id=%s group by 1,2", (pid,)).fetchall()
    total = sum(float(x["c"]) for x in led)
    step("ledger_matches_estimate", abs(total - est_full_free["total_usd"]) < 0.01,
         estimate_usd=est_full_free["total_usd"], ledger_usd=round(total, 6), rows=[dict(x) for x in led])

    # 7. publish gate: blocked until blur sign-off, fact-check, approval
    try:
        db.execute("insert into publishes (project_id, render_id, user_id) values (%s,%s,%s)", (pid, rid, uid))
        blocked = False
    except Exception as e:  # noqa: BLE001
        blocked = "publish blocked" in str(e)
    checklist = db.execute("select publish_checklist(%s,%s) c", (pid, rid)).fetchone()["c"]
    step("publish_blocked_before_review", blocked, failing=[i["key"] for i in checklist["items"] if not i["pass"]])
    try:
        db.execute("update projects set approved_at=now(), approved_by=%s where id=%s", (uid, pid))
        approve_blocked = False
    except Exception as e:  # noqa: BLE001
        approve_blocked = "blur" in str(e)
    step("approve_blocked_before_blur_signoff", approve_blocked)
    db.execute("insert into blur_signoffs (project_id, render_id, user_id, frames_checked, scrubbed_full_video, notes) values (%s,%s,%s,%s,true,'e2e')",
               (pid, rid, uid, int(acc["duration"] * 30)))
    db.execute("update projects set fact_check_signed_at=now(), fact_check_signed_by=%s, approved_at=now(), approved_by=%s where id=%s", (uid, uid, pid))
    checklist = db.execute("select publish_checklist(%s,%s) c", (pid, rid)).fetchone()["c"]
    step("checklist_passes_after_review", checklist["pass"], items={i["key"]: i["pass"] for i in checklist["items"]},
         title=db.execute("select selected_title from projects where id=%s", (pid,)).fetchone()["selected_title"])
    results["project_id"] = str(pid)
    results["render_id"] = str(rid)
    results["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (REPORT / "e2e-30min.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
