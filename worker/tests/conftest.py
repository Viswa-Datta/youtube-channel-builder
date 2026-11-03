"""pytest setup: a fresh Postgres database with the real migrations, env set before ycb is imported."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import harness as H  # noqa: E402

URL = H.fresh_db("ycb_unit")
os.environ.update(H.worker_env(URL, LOCAL_STORAGE_DIR="/tmp/ycb-unit-storage", WORK_DIR="/tmp/ycb-unit-work",
                               JOB_LEASE_SECONDS=5))
for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY"):
    os.environ.pop(k, None)

import pytest  # noqa: E402


@pytest.fixture()
def db():
    c = H.connect(URL)
    yield c
    c.close()


@pytest.fixture()
def user(db):
    import uuid
    uid = db.execute("insert into auth.users (email) values (%s) returning id", (f"u{uuid.uuid4().hex[:8]}@test",)).fetchone()["id"]
    return uid


@pytest.fixture()
def project(db, user):
    ch = db.execute("insert into channels (user_id, niche, target_minutes) values (%s,'True Crime Documentary',12) returning id", (user,)).fetchone()["id"]
    pid = db.execute("""insert into projects (user_id, channel_id, title, tier, target_minutes, llm_provider, llm_model)
                        values (%s,%s,'Test project','standard',12,'openai','gpt-4o-mini') returning id""", (user, ch)).fetchone()["id"]
    sid = db.execute("insert into scenes (project_id, idx, title) values (%s,0,'S0') returning id", (pid,)).fetchone()["id"]
    return {"user": user, "channel": ch, "project": pid, "scene": sid}
