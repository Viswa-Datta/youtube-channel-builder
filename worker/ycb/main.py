"""Worker entry point.

    python -m ycb.main worker            # run forever, claim jobs from Postgres
    python -m ycb.main estimate full standard 30
    python -m ycb.main check-env         # list which keys/accounts are missing
"""
from __future__ import annotations

import json
import signal
import sys
import time
import traceback

from . import db, pricing, queue, youtube
from .agents import core as agents
from .config import MissingConfig, settings
from .ledger import CapExceeded
from .providers.llm import AgentOutputInvalid
from .stages import pipeline

_stop = False


def _sig(*_):
    global _stop
    _stop = True


def delete_user_data(job: dict) -> dict:
    """Data-deletion path: revoke Google tokens, delete stored media, then the user's rows (cascade)."""
    from .storage import store
    uid = job["payload"]["user_id"]
    youtube.revoke(uid)
    for p in db.q("select id from projects where user_id=%s", (uid,)):
        store().delete_prefix(f"projects/{p['id']}")
    if job["payload"].get("delete_account"):
        db.x("delete from auth.users where id=%s", (uid,))  # cascades to users and every owned row
    return {"deleted": True}


def dispatch(job: dict, hb: queue.Heartbeat) -> None:
    kind = job["kind"]
    if kind == "channel_kit":
        agents.channel_kit(job)
    elif kind == "channel_kit_item":
        agents.channel_kit_item(job)
    elif kind == "ideas":
        agents.ideas(job)
    elif kind == "production_pack":
        agents.production_pack(job)
    elif kind == "render_stage":
        pipeline.run_stage(job, hb)
    elif kind == "animated_short":
        from .stages import animated
        animated.run_animated_short(job, hb)
    elif kind == "shorts":
        from .stages import shorts
        shorts.run_shorts(job, hb)
    elif kind == "publish":
        youtube.publish(job)
    elif kind == "delete_user_data":
        delete_user_data(job)
    else:
        raise ValueError(f"unknown job kind {kind}")


def _on_render_failure(job: dict, msg: str, final: bool) -> None:
    if job.get("render_id") and job["kind"] == "render_stage":  # publish/other jobs never change render state
        db.x("update renders set status=%s, error=%s where id=%s",
             ("failed" if final else "running", msg[:2000], job["render_id"]))
        if final:
            db.x("update projects set status='failed' where id=%s", (job["project_id"],))


_busy = 0
_last_active = time.time()


def _work_pending() -> bool:
    """Queued work due within 10 min (e.g. a retry waiting out its back-off) keeps the machine on."""
    r = db.q1("""select 1 from jobs where (status='queued' and run_after < now() + interval '10 minutes')
                 or (status='running' and locked_until > now()) limit 1""")
    return bool(r)


def run_one(kinds: list[str] | None = None, lane: str | None = None) -> bool:
    global _busy, _last_active
    job = queue.claim(kinds, lane)
    if not job:
        return False
    _busy += 1
    try:
        return _run_claimed(job, lane)
    finally:
        _busy -= 1
        _last_active = time.time()


def _run_claimed(job: dict, lane: str | None) -> bool:
    print(f"[{settings.worker_id}{'/' + lane if lane else ''}] claimed {job['kind']}:{job.get('stage') or ''} {job['id']} attempt {job['attempts']}", flush=True)
    with queue.Heartbeat(str(job["id"])) as hb:
        try:
            dispatch(job, hb)
            queue.complete(str(job["id"]))
            print(f"[{settings.worker_id}] done {job['kind']}:{job.get('stage') or ''}", flush=True)
        except queue.JobCancelled:
            queue.release_cancelled(str(job["id"]))
            print(f"[{settings.worker_id}] cancelled {job['kind']}:{job.get('stage') or ''}", flush=True)
        except MissingConfig as e:
            queue.fail_permanently(str(job["id"]), str(e))  # never fake output; tell the user which key is missing
            _on_render_failure(job, str(e), True)
        except CapExceeded as e:
            queue.pause_for_cap(str(job["id"]), str(e))
            if job.get("render_id") and job["kind"] == "render_stage":
                db.x("update renders set status='paused_cap', error=%s where id=%s", (str(e), job["render_id"]))
        except (youtube.QuotaExceeded,) as e:
            queue.fail_permanently(str(job["id"]), str(e))
            db.x("update publishes set status='failed', error=%s where id=%s", (str(e), job["payload"].get("publish_id")))
        except (AgentOutputInvalid, agents.BusinessRuleViolation, Exception) as e:
            msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-2000:]}"
            row = queue.fail(str(job["id"]), msg)
            if row is not None:  # None: the user cancelled this job, leave the render as cancelled
                _on_render_failure(job, f"{type(e).__name__}: {e}", row["status"] == "failed")
            print(f"[{settings.worker_id}] failed {job['kind']}: {e}", file=sys.stderr, flush=True)
    return True


def _loop(lane: str | None) -> None:
    global _stop
    backoff = 5
    while not _stop:
        try:
            if not run_one(lane=lane):
                idle_min = settings.idle_exit_minutes
                if (lane == "main" and idle_min > 0 and _busy == 0 and time.time() - _last_active > idle_min * 60
                        and not _work_pending()):
                    # Nothing to do: turn the machine off so Fly stops billing. The website starts it again when
                    # a new job is queued (needs FLY_API_TOKEN + FLY_APP in Vercel).
                    print(f"[{settings.worker_id}] idle for {idle_min} min: stopping the machine", flush=True)
                    _stop = True
                    break
                time.sleep(settings.poll_seconds)
            backoff = 5
        except Exception as e:  # noqa: BLE001 — database unreachable etc.: wait and retry, never crash-loop
            safe = settings.database_url.split("@")[-1]
            print(f"[{settings.worker_id}] cannot reach the database ({safe}): {type(e).__name__}: {e}. "
                  f"Retrying in {backoff}s. If this repeats, check DATABASE_URL (a password with @ # / : ? % must be URL-encoded).",
                  file=sys.stderr, flush=True)
            try:
                db.conn().close()
            except Exception:  # noqa: BLE001
                pass
            db._local.conn = None
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)


def worker() -> None:
    """Three lanes in one machine: 'voice' records narration, 'main' does the other render stages (footage, assembly),
    'quick' does ideas, channel kits, production packs and publishing, so a quick job never waits behind a video."""
    import threading
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    print(f"worker {settings.worker_id} polling {settings.database_url.split('@')[-1]} (lanes: main + voice + quick)", flush=True)
    lanes = [threading.Thread(target=_loop, args=(ln,), daemon=True, name=f"{ln}-lane") for ln in ("voice", "quick")]
    for t in lanes:
        t.start()
    _loop("main")
    for t in lanes:
        t.join(timeout=30)


def check_env() -> dict:
    out = {}
    for p in ["supabase", "openai", "anthropic", "gemini", "ollama", "elevenlabs", "google", "pexels", "pixabay", "youtube"]:
        out[p] = settings.missing_for(p) or "ok"
    for name, path in [("kokoro_model", settings.kokoro_model), ("kokoro_voices", settings.kokoro_voices),
                       ("face_model", settings.face_model), ("plate_model", settings.plate_model)]:
        out[name] = "ok" if path.exists() else f"missing file {path}"
    return out


def check_storage() -> dict:
    """Round-trip a tiny object through the configured storage driver (upload, exists, download, delete)."""
    import tempfile
    from pathlib import Path
    from .config import settings
    from .storage import store
    res: dict = {"driver": settings.storage_driver}
    key = "healthcheck/ycb-check.txt"
    try:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "in.txt"
            src.write_text("ok")
            store().put(src, key, "text/plain")
            res["upload"] = "ok"
            res["exists"] = store().exists(key)
            res["download"] = store().get(key, Path(d) / "out.txt").read_text() == "ok"
            store().delete_prefix("healthcheck")
            res["delete"] = not store().exists(key)
        res["word"] = "ready" if res["exists"] and res["download"] and res["delete"] else "problem"
    except Exception as e:  # noqa: BLE001 - report, never crash the CLI
        msg = str(e)
        for secret in (settings.r2_secret_access_key, settings.r2_access_key_id, settings.supabase_service_key):
            if secret:
                msg = msg.replace(secret, "[redacted]")
        res["error"] = f"{type(e).__name__}: {msg[:300]}"
        res["word"] = "problem"
    return res


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "worker"
    if cmd == "worker":
        worker()
    elif cmd == "estimate":
        print(json.dumps(pricing.estimate(sys.argv[2], sys.argv[3], int(sys.argv[4])), indent=2))
    elif cmd == "check-env":
        print(json.dumps(check_env(), indent=2))
    elif cmd == "check-llm":
        from .providers.llm import check_llm
        print(json.dumps(check_llm(), indent=2))
    elif cmd == "check-footage":
        from .footage import discovery
        q = " ".join(sys.argv[2:]) or "Phoenix Police critical incident bodycam"
        res = discovery.youtube(q, 8)
        print(json.dumps({"query": q, "youtube_key_set": bool(settings.youtube_api_key) if (settings := __import__("ycb.config", fromlist=["settings"]).settings) else False,
                          "youtube_error": discovery.LAST_ERRORS.get("youtube"), "results": len(res),
                          "official": [f"{e.channel} | {e.title[:70]}" for e in res if e.official],
                          "other": [f"{e.channel} | {e.title[:70]}" for e in res if not e.official]}, indent=2))
    elif cmd == "check-storage":
        print(json.dumps(check_storage(), indent=2))
    elif cmd == "once":
        while run_one():
            pass
