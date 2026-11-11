"""Cost estimator. Mirrors web/lib/pricing.ts exactly (tests compare both on the same inputs).

Every line shows stage, provider, plan/model, units, cost, and the free alternative beside it.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .config import load_json

P = load_json("config/pricing.json")


@dataclass
class Line:
    stage: str
    provider: str
    plan: str
    unit_type: str
    units: int
    cost_usd: float
    paid: bool
    free_alternative: str


def _llm_cost(key: str, tin: int, tout: int) -> float:
    m = P["llm"][key]
    return tin / 1e6 * m["in_per_m"] + tout / 1e6 * m["out_per_m"]


def _free_llm() -> str:
    return P["llm"][P["tiers"]["free"]["llm"]]["label"] + " — $0"


def _llm_line(stage: str, key: str, tin: int, tout: int) -> Line:
    m = P["llm"][key]
    return Line(stage, key.split(":")[0], m["label"], "tokens", tin + tout, round(_llm_cost(key, tin, tout), 6),
                m["paid"], "" if not m["paid"] else _free_llm())


def words_for(minutes: int) -> int:
    return int(round(minutes * P["words_per_minute"]))


def voice_chars(minutes: int) -> int:
    return int(round(words_for(minutes) * P["chars_per_word"]))


def estimate(action: str, tier: str, minutes: int = 12, scenes: int = 7, llm: str | None = None,
             tts: str | None = None, compute: str | None = None) -> dict:
    t = P["tiers"][tier]
    llm = llm or t["llm"]
    tts = tts or t["tts"]
    compute = compute or t["compute"]
    polish = t["polish_llm"]
    b = P["agent_token_budgets"]
    tpw = P["tokens_per_word"]
    words = words_for(minutes)
    lines: list[Line] = []

    if action in ("channel_kit",):
        lines.append(_llm_line("Channel Agent", llm, b["channel_kit"]["in"], b["channel_kit"]["out"]))
    if action == "channel_kit_item":
        lines.append(_llm_line("Channel Agent (one item)", llm, b["channel_kit_item"]["in"], b["channel_kit_item"]["out"]))
    if action == "ideas":
        lines.append(_llm_line("Ideas Agent (25 ideas)", llm, b["ideas"]["in"], b["ideas"]["out"]))
    if action in ("production_pack", "full"):
        lines.append(_llm_line("Research Agent", llm, b["research"]["in"], b["research"]["out"]))
        lines.append(_llm_line("Script Agent (draft)", llm, b["script_draft"]["in"], b["script_draft"]["out"]))
        lines.append(_llm_line("Visual Director Agent", llm, b["visual_director"]["in"], b["visual_director"]["out"]))
        lines.append(Line("Footage search (archives, Pexels, Pixabay)", "footage", "Free APIs", "requests", scenes * 4, 0.0, False, ""))
    if action == "shorts":  # minutes = number of Shorts here (about 50 s each)
        n = max(1, minutes)
        lines.append(_llm_line(f"Shorts Agent ({n} scripts)", llm, b["shorts"]["in"], b["shorts"]["out"] * n))
        chars = int(n * 130 * P["chars_per_word"])
        v = P["tts"][tts]
        lines.append(Line("Voice Agent (Shorts narration)", tts.split(":")[0], v["label"], "characters", chars,
                          round(chars / 1e6 * v["per_m_chars"], 6), v["paid"],
                          "" if not v["paid"] else P["tts"][P["tiers"]["free"]["tts"]]["label"] + " — $0"))
        ai = P["ai_images"]
        k = n * ai["per_short"]
        lines.append(Line("AI illustrations (only where no real picture fits; at most)", "gemini", ai["label"], "requests", k,
                          round(k * ai["per_image"], 6), True, "Real photos and clips only — $0"))
        c = P["compute"][compute]
        hours = n * P["render_hours_per_video_minute"]
        lines.append(Line("Render worker (vertical cut, mix)", "compute", c["label"], "seconds", int(hours * 3600),
                          round(hours * c["per_render_hour"], 6), c["paid"],
                          "" if not c["paid"] else P["compute"]["self_hosted"]["label"] + " — $0"))
    if action in ("whole_video", "full"):
        tin = scenes * b["script_expand_overhead_in_per_scene"]
        tout = int(words * tpw)
        lines.append(_llm_line("Script Agent (expand to full narration)", llm, tin, tout))
        if polish:
            lines.append(_llm_line("Script polish pass", polish, int(words * tpw) + 1500, int(words * tpw)))
        lines.append(_llm_line("Editor + SEO Agent", llm, int(words * tpw) + b["editor_seo_overhead"]["in"], b["editor_seo_overhead"]["out"]))
        chars = voice_chars(minutes)
        v = P["tts"][tts]
        lines.append(Line("Voice Agent (narration)", tts.split(":")[0], v["label"], "characters", chars,
                          round(chars / 1e6 * v["per_m_chars"], 6), v["paid"],
                          "" if not v["paid"] else P["tts"][P["tiers"]["free"]["tts"]]["label"] + " — $0"))
        c = P["compute"][compute]
        hours = minutes * P["render_hours_per_video_minute"]
        lines.append(Line("Render worker (FFmpeg, blur, mix)", "compute", c["label"], "seconds", int(hours * 3600),
                          round(hours * c["per_render_hour"], 6), c["paid"],
                          "" if not c["paid"] else P["compute"]["self_hosted"]["label"] + " — $0"))
        s = P["storage"]["supabase_or_r2"]
        gb = minutes * s["gb_per_minute_1080p"] * 1.5  # render + intermediates kept 1 month
        free = tier == "free"
        lines.append(Line("Storage (1 month)", "storage", "Local disk / Supabase free 1 GB" if free else s["label"], "gb",
                          math.ceil(gb * 1000), 0.0 if free else round(gb * s["per_gb_month"], 6), not free,
                          "" if free else "Local disk / Supabase free 1 GB — $0"))

    total = round(sum(l.cost_usd for l in lines), 4)
    per10 = round(total / minutes * 10, 4) if minutes else total
    guard = None
    if tier == "standard" and action in ("whole_video", "full") and per10 > P["guardrail"]["standard_max_usd_per_10_min"]:
        guard = f"Standard must stay under ${P['guardrail']['standard_max_usd_per_10_min']:.2f} per 10 min; move this feature to Premium."
    return {
        "action": action, "tier": tier, "minutes": minutes, "scenes": scenes,
        "words": words, "voice_characters": voice_chars(minutes),
        "lines": [asdict(l) for l in lines], "total_usd": total, "per_10_min_usd": per10,
        "guardrail_violation": guard, "prices_last_verified": P["last_verified"],
        "math": f"{minutes} min x {P['words_per_minute']} wpm = {words} words x {P['chars_per_word']} chars = {voice_chars(minutes)} voice characters",
    }


def llm_cost(provider: str, model: str, tin: int, tout: int) -> float:
    key = f"{provider}:{model}"
    if key not in P["llm"]:
        raise KeyError(f"no price configured for {key} in config/pricing.json")
    return _llm_cost(key, tin, tout)


def tts_cost(provider: str, model_or_voice: str, chars: int) -> float:
    key = f"{provider}:{model_or_voice}"
    if key not in P["tts"]:
        key = next((k for k in P["tts"] if k.startswith(provider + ":")), None)
        if key is None:
            raise KeyError(f"no price configured for TTS provider {provider}")
    return chars / 1e6 * P["tts"][key]["per_m_chars"]
