"""Cancelling a video: the worker stops at its next progress update, never retries, never starts the next stage."""
import pytest

from ycb import db, queue
from ycb.config import settings


def _job(u, status="running"):
    return db.q1("""insert into jobs (user_id, kind, status, locked_by, locked_until, attempts)
                    values (%s, 'render_stage', %s, %s, now() + interval '5 min', 1) returning *""",
                 (u, status, settings.worker_id))


def test_check_raises_after_cancel(user):
    j = _job(user)
    hb = queue.Heartbeat(str(j["id"]))
    hb.check(force=True)  # still running: no error
    db.x("update jobs set status='cancelled' where id=%s", (j["id"],))
    with pytest.raises(queue.JobCancelled):
        hb.check(force=True)


def test_cancelled_job_is_not_retried_or_completed(user):
    j = _job(user)
    db.x("update jobs set status='cancelled' where id=%s", (j["id"],))
    assert queue.fail(str(j["id"]), "boom") is None
    queue.complete(str(j["id"]))
    row = db.q1("select status, locked_by from jobs where id=%s", (j["id"],))
    assert row["status"] == "cancelled" and row["locked_by"] is None
