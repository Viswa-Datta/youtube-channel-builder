"""Test harness: a fresh Postgres database with the real migrations, and a worker subprocess."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

REPO = Path(__file__).resolve().parents[2]
PG_HOST = os.environ.get("TEST_PG_HOST", "/tmp/pg")
PG_PORT = os.environ.get("TEST_PG_PORT", "54322")


def dsn(db: str) -> str:
    return f"postgresql://postgres@/{db}?host={PG_HOST}&port={PG_PORT}"


def fresh_db(name: str) -> str:
    admin = psycopg.connect(dsn("postgres"), autocommit=True)
    admin.execute(f"drop database if exists {name} with (force)")
    admin.execute(f"create database {name}")
    admin.close()
    c = psycopg.connect(dsn(name), autocommit=True)
    for f in ["supabase/local/auth_stub.sql", *sorted(str(p.relative_to(REPO)) for p in (REPO / "supabase/migrations").glob("*.sql"))]:
        c.execute((REPO / f).read_text())
    c.close()
    return dsn(name)


def connect(url: str):
    return psycopg.connect(url, row_factory=dict_row, autocommit=True)


def worker_env(url: str, **extra) -> dict:
    env = dict(os.environ)
    env.update({
        "DATABASE_URL": url, "PYTHONPATH": f"{os.environ.get('VENDOR', '/home/claude/vendor')}:{REPO / 'worker'}",
        "ALLOW_FIXTURES": "true", "STORAGE_DRIVER": "local",
        "KOKORO_MODEL": "/models/kokoro-v1.0.onnx", "KOKORO_VOICES": "/models/voices-v1.0.bin",
        "KOKORO_VOCAB": "/models/kokoro-vocab.json", "ESPEAK_BIN": os.environ.get("ESPEAK_BIN", "/opt/espeak-ng/bin/espeak-ng"),
        "FACE_MODEL": "/models/face_detection_yunet_2023mar.onnx",
        "PLATE_MODEL": "/models/yolo-v9-t-384-license-plates-end2end.onnx",
    })
    env.update({k: str(v) for k, v in extra.items()})
    return env


class Worker:
    def __init__(self, env: dict, log: Path, name: str):
        self.env = {**env, "WORKER_ID": name}
        self.log = log
        self.p: subprocess.Popen | None = None

    def start(self) -> "Worker":
        self.p = subprocess.Popen([sys.executable, "-m", "ycb.main", "worker"], env=self.env, cwd=REPO / "worker",
                                  stdout=open(self.log, "a"), stderr=subprocess.STDOUT)
        return self

    def kill(self) -> None:
        """SIGKILL: no cleanup, no lease release — simulates a crashed machine."""
        if self.p and self.p.poll() is None:
            self.p.send_signal(signal.SIGKILL)
            self.p.wait()

    def stop(self) -> None:
        if self.p and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.kill()


def wait_for(cond, timeout: float, every: float = 2.0, what: str = ""):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(every)
    raise TimeoutError(f"timed out waiting for {what}")
