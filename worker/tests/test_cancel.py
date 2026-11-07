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


def test_lanes_split_voice_from_everything_else(user):
    """The voice lane only takes voice recording; the main lane never does, so short steps never wait behind it."""
    v = db.q1("""insert into jobs (user_id, kind, stage, priority) values (%s, 'render_stage', 'voice', 1) returning id""", (user,))["id"]
    o = db.q1("""insert into jobs (user_id, kind, stage, priority) values (%s, 'render_stage', 'assemble', 2) returning id""", (user,))["id"]
    q = db.q1("""insert into jobs (user_id, kind, priority) values (%s, 'ideas', 3) returning id""", (user,))["id"]
    main_job = queue.claim(lane="main")
    voice_job = queue.claim(lane="voice")
    quick_job = queue.claim(lane="quick")
    assert main_job["id"] == o and voice_job["id"] == v and quick_job["id"] == q
    assert queue.claim(lane="voice") is None
