"""Captions (.srt) from real sentence timings, with personal data omitted."""
from __future__ import annotations

import re
import textwrap


def ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


PII_PATTERNS = [
    re.compile(r"\b\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b"),                     # phone numbers
    re.compile(r"\b\d{1,5}\s+[A-Z][a-z]+(\s[A-Z][a-z]+)*\s(Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Court|Ct|Boulevard|Blvd)\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                                    # SSN-like
]


def scrub(text: str, omit_terms: list[str]) -> str:
    for term in omit_terms:
        if term.strip():
            text = re.sub(re.escape(term), "[withheld]", text, flags=re.IGNORECASE)
    for p in PII_PATTERNS:
        text = p.sub("[withheld]", text)
    return text


def split_cue(text: str, start: float, end: float, max_chars: int = 84) -> list[tuple[float, float, str]]:
    """Long sentences become several cues of <= 2 lines x 42 chars, timed proportionally to characters."""
    if len(text) <= max_chars:
        return [(start, end, text)]
    words, parts, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > max_chars and cur:
            parts.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        parts.append(cur)
    total = sum(len(p) for p in parts)
    out, t = [], start
    for p in parts:
        d = (end - start) * len(p) / total
        out.append((t, t + d, p))
        t += d
    return out


def build_srt(sentences: list[dict], omit_terms: list[str] | None = None) -> str:
    """sentences: [{text, start, end}] absolute programme time."""
    lines, n = [], 0
    for s in sentences:
        for a, b, txt in split_cue(scrub(s["text"], omit_terms or []), s["start"], s["end"]):
            n += 1
            wrapped = "\n".join(textwrap.wrap(txt, 42)[:2])
            lines.append(f"{n}\n{ts(a)} --> {ts(max(b - 0.02, a + 0.3))}\n{wrapped}\n")
    return "\n".join(lines)
