"""LLM providers behind one call: complete_json(). Output is always validated by a pydantic model;
an invalid answer is retried once with the validation error, then the run fails (nothing downstream runs).
Every call is logged to cost_ledger with model, tokens, cost and duration.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import requests
from pydantic import BaseModel, ValidationError

from .. import ledger, pricing
from ..config import REPO_ROOT, MissingConfig, require, settings

T = TypeVar("T", bound=BaseModel)


@dataclass
class Usage:
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    ms: int


class AgentOutputInvalid(RuntimeError):
    pass


def _openai(model: str, system: str, user: str, schema: dict, name: str) -> tuple[str, int, int]:
    require("openai")
    r = requests.post("https://api.openai.com/v1/chat/completions", timeout=300,
                      headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                      json={"model": model, "temperature": 0.6,
                            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                            "response_format": {"type": "json_schema",
                                                "json_schema": {"name": name, "schema": schema, "strict": False}}})
    r.raise_for_status()
    d = r.json()
    return d["choices"][0]["message"]["content"], d["usage"]["prompt_tokens"], d["usage"]["completion_tokens"]


def _anthropic(model: str, system: str, user: str, schema: dict, name: str) -> tuple[str, int, int]:
    require("anthropic")
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=300,
                      headers={"x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
                      json={"model": model, "max_tokens": 16000, "system": system,
                            "messages": [{"role": "user", "content": user}],
                            "tools": [{"name": name, "description": "Return the result", "input_schema": schema}],
                            "tool_choice": {"type": "tool", "name": name}})
    r.raise_for_status()
    d = r.json()
    block = next(b for b in d["content"] if b["type"] == "tool_use")
    return json.dumps(block["input"]), d["usage"]["input_tokens"], d["usage"]["output_tokens"]


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _gemini_models() -> list[str]:
    import os
    first = os.environ.get("GEMINI_MODEL") or ""
    fallbacks = [m.strip() for m in os.environ.get("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-flash-lite-latest").split(",")]
    return [m for m in dict.fromkeys([first, *fallbacks]) if m]


def _gemini(model: str, system: str, user: str, schema: dict, name: str) -> tuple[str, int, int]:
    """Key goes in the x-goog-api-key header (never the URL, so it cannot leak into logs or error messages).
    Busy/overloaded responses (429/500/503) are retried with backoff, then the next fallback model is tried."""
    import time as _t
    require("gemini")
    prompt = f"{user}\n\nReturn ONLY JSON that validates against this JSON Schema:\n{json.dumps(schema)}"
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.6}}
    last = ""
    for m in (_gemini_models() or [model]):
        for attempt in range(4):
            r = requests.post(f"{GEMINI_BASE}/models/{m}:generateContent", timeout=300, json=body,
                              headers={"x-goog-api-key": settings.gemini_api_key})
            if r.status_code in (429, 500, 503):
                last = f"{m}: HTTP {r.status_code} (Google busy)"
                _t.sleep(min(60, 5 * 2 ** attempt))
                continue
            if r.status_code == 404:
                last = f"{m}: model not found"
                break
            if not r.ok:
                raise RuntimeError(f"Gemini {m} returned HTTP {r.status_code}: {r.text[:300]}")
            d = r.json()
            text = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
            u = d.get("usageMetadata", {})
            return text, u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)
    raise RuntimeError(f"Gemini unavailable after retries and fallbacks ({last})")


def _ollama(model: str, system: str, user: str, schema: dict, name: str) -> tuple[str, int, int]:
    require("ollama")
    r = requests.post(settings.ollama_url.rstrip("/") + "/api/chat", timeout=900,
                      json={"model": model, "stream": False, "format": schema,
                            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    r.raise_for_status()
    d = r.json()
    return d["message"]["content"], d.get("prompt_eval_count", 0), d.get("eval_count", 0)


def _fixture(model: str, system: str, user: str, schema: dict, name: str, fixture: str | None = None) -> tuple[str, int, int]:
    """Test-only provider: returns pre-authored JSON from fixtures/. Refuses to run unless ALLOW_FIXTURES=true."""
    if not settings.allow_fixtures:
        raise MissingConfig("llm", ["OPENAI_API_KEY or ANTHROPIC_API_KEY or GEMINI_API_KEY or OLLAMA_URL"])
    path = REPO_ROOT / "fixtures" / (fixture or "")
    if not path.exists():
        raise FileNotFoundError(f"fixture {path} not found")
    return path.read_text(encoding="utf-8"), 0, 0


PROVIDERS = {"openai": _openai, "anthropic": _anthropic, "gemini": _gemini, "ollama": _ollama}


def complete_json(*, out: type[T], agent: str, provider: str, model: str, system: str, user: str,
                  user_id: str, idem: str, project_id: str | None = None, render_id: str | None = None,
                  fixture: str | None = None, stage: str | None = None) -> tuple[T, Usage]:
    """Run one agent call, validate, log to the ledger. Raises AgentOutputInvalid after a corrective retry."""
    schema = out.model_json_schema()
    name = out.__name__
    attempts = 0
    tin_total = tout_total = 0
    started = time.time()
    feedback = ""
    while True:
        attempts += 1
        if provider == "fixture":
            raw, tin, tout = _fixture(model, system, user, schema, name, fixture)
        else:
            if provider not in PROVIDERS:
                raise ValueError(f"unknown LLM provider {provider}")
            raw, tin, tout = PROVIDERS[provider](model, system, user + feedback, schema, name)
        tin_total += tin
        tout_total += tout
        try:
            obj = out.model_validate_json(_strip_fences(raw))
            break
        except ValidationError as e:
            if attempts >= 2:
                _log(agent, provider, model, tin_total, tout_total, started, user_id, idem, project_id, render_id, stage,
                     ok=False)
                raise AgentOutputInvalid(f"{agent} returned JSON that failed schema {name}: {e}") from e
            feedback = ("\n\nYour previous answer failed validation with these errors; fix them and return the "
                        f"complete JSON again:\n{e}")
    usage = _log(agent, provider, model, tin_total, tout_total, started, user_id, idem, project_id, render_id, stage, ok=True)
    return obj, usage


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1].rsplit("```", 1)[0]
    return s


def _log(agent, provider, model, tin, tout, started, user_id, idem, project_id, render_id, stage, ok) -> Usage:
    ms = int((time.time() - started) * 1000)
    try:
        cost = pricing.llm_cost(provider, model, tin, tout)
    except KeyError:
        cost = 0.0
    ledger.record(user_id=user_id, project_id=project_id, render_id=render_id, stage=stage or agent, agent=agent,
                  provider=provider, model=model, unit_type="tokens", units_in=tin, units_out=tout,
                  cost_usd=cost, duration_ms=ms, idempotency_key=idem + ("" if ok else ":invalid"),
                  meta={"valid": ok})
    return Usage(provider, model, tin, tout, ms)


def check_llm() -> dict:
    """Tiny live call to each configured LLM provider: proves the key works before real jobs run (costs ~nothing)."""
    import os
    from pydantic import BaseModel as _BM
    out: dict = {}

    class Ping(_BM):
        ok: bool
        word: str

    if settings.gemini_api_key:
        r = requests.get(f"{GEMINI_BASE}/models", params={"pageSize": 200}, headers={"x-goog-api-key": settings.gemini_api_key}, timeout=30)
        names = [m["name"].split("/", 1)[1] for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])] if r.ok else []
        model = os.environ.get("GEMINI_MODEL") or "gemini-3.1-flash-lite"
        res = {"key_valid": r.ok, "model": model, "model_available": model in names,
               "flash_models_available": [n for n in names if "flash" in n][:15]}
        if r.ok and model in names:
            try:
                txt, tin, tout = _gemini(model, "Reply with JSON only.", 'Return {"ok": true, "word": "ready"}', Ping.model_json_schema(), "Ping")
                res["test_call"] = Ping.model_validate_json(_strip_fences(txt)).model_dump()
            except Exception as e:  # noqa: BLE001
                res["test_call_error"] = str(e)[:300].replace(settings.gemini_api_key or "\x00", "[redacted]")
        out["gemini"] = res
    if settings.openai_api_key:
        r = requests.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {settings.openai_api_key}"}, timeout=30)
        out["openai"] = {"key_valid": r.ok}
    return out
