"""Postgres-backed job queue: SKIP LOCKED claims, leases with heartbeats, retries with backoff.

Why Postgres and not BullMQ/Redis: Supabase already gives us Postgres, so there is no extra
account or bill; enqueue can be transactional with the rows it refers to; and Upstash's free
Redis tier meters every command, which BullMQ's polling burns through. A crashed worker's
lease expires and any other worker resumes the job (see claim_job in 0002_functions.sql).
"""
from __future__ import annotations

import threading
import time
from typing import Any

from . import db
from .config import settings


def enqueue(*, user_id: str, kind: str, payload: dict | None = None, project_id: str | None = None,
            render_id: str | None = None, stage: str | None = None, idempotency_key: str | None = None,
            priority: int = 100, max_attempts: int = 4) -> dict | None:
    """Insert a job. Re-enqueueing with the same idempotency_key is a no-op (returns existing)."""
    row = db.q1(
        """insert into jobs (user_id, project_id, render_id, kind, stage, payload, idempotency_key, priority, max_attempts)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
           on conflict (idempotency_key) do nothing returning *""",
        (user_id, project_id, render_id, kind, stage, db.jsonb(payload or {}), idempotency_key, priority, max_attempts))
    if row is None and idempotency_key:
        row = db.q1("select * from jobs where idempotency_key = %s", (idempotency_key,))
    return row


def claim(kinds: list[str] | None = None) -> dict | None:
    return db.q1("select * from claim_job(%s::text, %s::int, %s::text[])", (settings.worker_id, settings.lease_seconds, kinds))


class JobCancelled(Exception):
    """The user pressed Cancel: stop work, keep the job marked cancelled."""


def complete(job_id: str) -> None:
    db.x("""update jobs set status='succeeded', progress=100, finished_at=now(), locked_by=null, locked_until=null
            where id=%s and locked_by=%s and status='running'""", (job_id, settings.worker_id))


def release_cancelled(job_id: str) -> None:
    db.x("update jobs set locked_by=null, locked_until=null, finished_at=coalesce(finished_at, now()) where id=%s", (job_id,))


def fail(job_id: str, error: str) -> dict | None:
    st = db.q1("select status from jobs where id=%s", (job_id,))
    if st and st["status"] == "cancelled":  # an error after the user cancelled is not retried
        release_cancelled(job_id)
        return None
    return db.q1("select * from fail_job(%s::uuid, %s::text, %s::text)", (job_id, settings.worker_id, error))


def fail_permanently(job_id: str, error: str) -> None:
    db.x("""update jobs set status='failed', last_error=%s, finished_at=now(), locked_by=null, locked_until=null
            where id=%s""", (error[:4000], job_id))


def pause_for_cap(job_id: str, error: str) -> None:
    """Hard cap reached: the job waits (not failed, not retried) until the user raises the cap."""
    db.x("""update jobs set status='paused_cap', last_error=%s, locked_by=null, locked_until=null,
            attempts=greatest(attempts-1,0) where id=%s""", (error, job_id))


class Heartbeat:
    """Keeps the lease alive while a long stage runs and records progress for the UI."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.progress: float | None = None
        self.note: str | None = None
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self.lost = False
        self.cancelled = False
        self._checked = 0.0

    def __enter__(self) -> "Heartbeat":
        self._t.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        self._t.join(timeout=5)

    def update(self, progress: float | None = None, note: str | None = None) -> None:
        self.progress, self.note = progress, note
        self._beat()

    def _beat(self) -> None:
        c = db.connect()
        try:
            r = c.execute("select heartbeat_job(%s::uuid,%s::text,%s::int,%s::numeric,%s::text) as ok",
                          (self.job_id, settings.worker_id, settings.lease_seconds, self.progress, self.note)).fetchone()
            if not (r and r["ok"]):
                self.lost = True
                st = c.execute("select status from jobs where id=%s::uuid", (self.job_id,)).fetchone()
                self.cancelled = bool(st and st["status"] == "cancelled")
        finally:
            c.close()

    def check(self, force: bool = False) -> None:
        """Raise JobCancelled if the user cancelled. Looks at the database at most every 15 s unless forced."""
        if not self.cancelled and (force or time.time() - self._checked > 15):
            self._checked = time.time()
            r = db.q1("select status from jobs where id=%s", (self.job_id,))
            self.cancelled = bool(r and r["status"] == "cancelled")
        if self.cancelled:
            raise JobCancelled("cancelled by the user")

    def _run(self) -> None:
        interval = max(5, settings.lease_seconds // 3)
        while not self._stop.wait(interval):
            self._beat()


def wait_until_idle(timeout: float = 600) -> None:
    end = time.time() + timeout
    while time.time() < end:
        r = db.q1("select count(*) n from jobs where status in ('queued','running')")
        if r["n"] == 0:
            return
        time.sleep(1)
