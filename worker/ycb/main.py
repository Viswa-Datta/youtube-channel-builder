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


def run_one(kinds: list[str] | None = None) -> bool:
    job = queue.claim(kinds)
    if not job:
        return False
    print(f"[{settings.worker_id}] claimed {job['kind']}:{job.get('stage') or ''} {job['id']} attempt {job['attempts']}", flush=True)
    with queue.Heartbeat(str(job["id"])) as hb:
        try:
            dispatch(job, hb)
            queue.complete(str(job["id"]))
            print(f"[{settings.worker_id}] done {job['kind']}:{job.get('stage') or ''}", flush=True)
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
            _on_render_failure(job, f"{type(e).__name__}: {e}", bool(row and row["status"] == "failed"))
            print(f"[{settings.worker_id}] failed {job['kind']}: {e}", file=sys.stderr, flush=True)
    return True


def worker() -> None:
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    print(f"worker {settings.worker_id} polling {settings.database_url.split('@')[-1]}", flush=True)
    backoff = 5
    while not _stop:
        try:
            if not run_one():
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


def check_env() -> dict:
    out = {}
    for p in ["supabase", "openai", "anthropic", "gemini", "ollama", "elevenlabs", "google", "pexels", "pixabay", "youtube"]:
        out[p] = settings.missing_for(p) or "ok"
    for name, path in [("kokoro_model", settings.kokoro_model), ("kokoro_voices", settings.kokoro_voices),
                       ("face_model", settings.face_model), ("plate_model", settings.plate_model)]:
        out[name] = "ok" if path.exists() else f"missing file {path}"
    return out


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
    elif cmd == "once":
        while run_one():
            pass
