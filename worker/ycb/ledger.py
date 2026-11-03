"""Cost ledger + hard monthly cap.

record() is idempotent on idempotency_key, so a retried stage that re-runs a call it already
paid for never writes a second charge. Stages also skip provider calls whose output already
exists, so retries don't re-bill at the provider either.
"""
from __future__ import annotations

import time
from contextlib import contextmanager

from . import db


class CapExceeded(RuntimeError):
    pass


def month_spend(user_id: str) -> float:
    return float(db.q1("select month_spend_usd(%s::uuid) s", (user_id,))["s"])


def ensure_within_cap(user_id: str, amount: float, what: str) -> None:
    r = db.q1("select within_cap(%s::uuid,%s::numeric) ok, (select monthly_cap_usd from users where id=%s::uuid) cap, month_spend_usd(%s::uuid) spent",
              (user_id, amount, user_id, user_id))
    if not r["ok"]:
        raise CapExceeded(
            f"Paused before {what}: this month ${float(r['spent']):.2f} + next step ${amount:.2f} "
            f"would exceed your hard cap ${float(r['cap']):.2f}. Raise the cap in Settings to continue.")


def record(*, user_id: str, stage: str, provider: str, unit_type: str, cost_usd: float, idempotency_key: str,
           project_id: str | None = None, render_id: str | None = None, agent: str | None = None,
           model: str | None = None, units_in: int = 0, units_out: int = 0, duration_ms: int | None = None,
           meta: dict | None = None) -> bool:
    n = db.x("""insert into cost_ledger (user_id, project_id, render_id, stage, agent, provider, model, unit_type,
                    units_in, units_out, cost_usd, duration_ms, idempotency_key, meta)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (idempotency_key) do nothing""",
             (user_id, project_id, render_id, stage, agent, provider, model, unit_type, units_in, units_out,
              round(cost_usd, 6), duration_ms, idempotency_key, db.jsonb(meta or {})))
    return n == 1


def already_charged(idempotency_key: str) -> bool:
    return db.q1("select 1 from cost_ledger where idempotency_key=%s", (idempotency_key,)) is not None


@contextmanager
def timer():
    t = {"start": time.time(), "ms": 0}
    yield t
    t["ms"] = int((time.time() - t["start"]) * 1000)
