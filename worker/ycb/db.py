"""Thin Postgres helpers (psycopg 3). The worker uses the service connection (bypasses RLS)."""
from __future__ import annotations

import contextlib
import json
import threading
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import settings

_local = threading.local()


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=True)


def conn() -> psycopg.Connection:
    c = getattr(_local, "conn", None)
    if c is None or c.closed:
        c = connect()
        _local.conn = c
    return c


def q(sql: str, params: Any = None) -> list[dict]:
    with conn().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def q1(sql: str, params: Any = None) -> dict | None:
    rows = q(sql, params)
    return rows[0] if rows else None


def x(sql: str, params: Any = None) -> int:
    with conn().cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


@contextlib.contextmanager
def tx() -> Iterator[psycopg.Connection]:
    c = conn()
    with c.transaction():
        yield c


def jsonb(v: Any) -> Jsonb:
    return Jsonb(v)


def dumps(v: Any) -> str:
    return json.dumps(v, default=str)
