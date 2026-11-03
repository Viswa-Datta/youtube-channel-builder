"""Selective re-render: edit one scene's narration after review, render v2, and check that only that scene is
re-voiced and recomposed (other scenes' audio and visuals are reused), with no duplicate charges."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import harness as H  # noqa: E402

REPORT = H.REPO / "reports"


def main():
    url = H.dsn("ycb_e2e")
    db = H.connect(url)
    p = db.execute("select * from projects order by created_at desc limit 1").fetchone()
    pid, uid = p["id"], p["user_id"]
    before = {r["idx"]: dict(r) for r in db.execute("select idx, id, narration, audio_hash, visual_hash, visual_path from scenes where project_id=%s", (pid,)).fetchall()}
    voice_rows_before = db.execute("select count(*) n from cost_ledger where project_id=%s and stage='voice'", (pid,)).fetchone()["n"]
    edited = 4
    s4 = before[edited]
    new_text = s4["narration"].replace("A laboratory match feels like an ending.",
                                       "A laboratory match feels like an ending. It is worth slowing down here.")
    assert new_text != s4["narration"]
    db.execute("update scenes set narration=%s where id=%s", (new_text, s4["id"]))
    db.execute("update projects set approved_at=null, fact_check_signed_at=null where id=%s", (pid,))
    db.execute("delete from blur_signoffs where project_id=%s", (pid,))
    v = db.execute("select coalesce(max(version),0)+1 v from renders where project_id=%s", (pid,)).fetchone()["v"]
    rid = db.execute("insert into renders (project_id, version) values (%s,%s) returning id", (pid, v)).fetchone()["id"]
    db.execute("insert into jobs (user_id, project_id, render_id, kind, stage, idempotency_key, priority) values (%s,%s,%s,'render_stage','script',%s,50)",
               (uid, pid, rid, f"render:{rid}:script"))
    env = H.worker_env(url, LOCAL_STORAGE_DIR=str(H.REPO / ".storage-e2e"), WORK_DIR="/tmp/ycb-e2e-work", JOB_LEASE_SECONDS=20, JOB_POLL_SECONDS=1)
    log = REPORT / "e2e-rerender-worker.log"
    log.write_text("")
    w = H.Worker(env, log, "worker-R").start()
    t0 = time.time()
    done = H.wait_for(lambda: (r := db.execute("select * from renders where id=%s", (rid,)).fetchone())["status"] in ("succeeded", "failed") and r,
                      3 * 3600, 10, "v2 render")
    w.stop()
    after = {r["idx"]: dict(r) for r in db.execute("select idx, audio_hash, visual_hash from scenes where project_id=%s", (pid,)).fetchall()}
    voice_rows_after = db.execute("select count(*) n from cost_ledger where project_id=%s and stage='voice'", (pid,)).fetchone()["n"]
    revoiced = [i for i in after if after[i]["audio_hash"] != before[i]["audio_hash"]]
    recomposed = [i for i in after if after[i]["visual_hash"] != before[i]["visual_hash"]]
    from ycb.media.ff import acceptance
    acc = acceptance(H.REPO / ".storage-e2e" / done["mp4_path"]) if done["status"] == "succeeded" else {}
    res = {"pass": done["status"] == "succeeded" and revoiced == [edited] and recomposed == [edited] and voice_rows_after == voice_rows_before + 1,
           "render_version": v, "status": done["status"], "error": done["error"], "minutes": round((time.time() - t0) / 60, 1),
           "scenes_revoiced": revoiced, "scenes_recomposed": recomposed, "voice_ledger_rows_before": voice_rows_before,
           "voice_ledger_rows_after": voice_rows_after, "export": acc}
    (REPORT / "e2e-rerender.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
