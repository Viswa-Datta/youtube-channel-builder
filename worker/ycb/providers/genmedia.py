"""AI-generated illustrations for moments real archives cannot show (e.g. a skyscraper rising as a scale model).

Images: Gemini 3.1 Flash Lite Image (Nano Banana 2 Lite), about $0.034 per 1K image.
Video:  Gemini Omni 1.2 Flash via the Interactions API, about $0.07 per second at 720p.
Both need the Gemini API paid tier (billing on the Google AI Studio project); without it the app simply skips
generation. Every generated item is labelled "AI illustration" on screen and in the credits.
"""
from __future__ import annotations

import base64
import time

import requests

from .. import ledger
from ..config import settings

API = "https://generativelanguage.googleapis.com/v1beta"
IMAGE_MODEL = "gemini-3.1-flash-lite-image"
VIDEO_MODEL = "gemini-omni-1.2-flash"
IMAGE_USD = 0.034
VIDEO_USD_PER_S = 0.07
STYLE = ("Highly detailed handcrafted miniature scale model diorama, tilt-shift photography, shallow depth of field, "
         "soft studio light, documentary illustration. Tiny generic figures only, no recognizable real people, no text, "
         "no logos, no watermarks.")


# Animated Shorts channels: a stylized 3D animated look (original characters only).
ANIMATED_STYLE = ("Stylized 3D animated render, like a frame from a modern animated feature: clean shapes, soft global "
                  "illumination, warm natural light, rich detail, cinematic composition. Interiors are shown as a clean "
                  "architectural cut-away section when that explains the idea. No text, no captions, no letters, no "
                  "logos, no watermarks, no real people.")


class GenUnavailable(Exception):
    """Generation is not available (no key, no billing on the key, model not offered): skip it for this run."""


def _headers() -> dict:
    if not settings.gemini_api_key:
        raise GenUnavailable("GEMINI_API_KEY is not set")
    return {"x-goog-api-key": settings.gemini_api_key, "Content-Type": "application/json"}


def _raise_for(r: requests.Response) -> None:
    if r.status_code in (400, 401, 403, 404, 429) or r.status_code >= 500:
        txt = r.text[:400]
        if r.status_code in (401, 403, 404) or "billing" in txt.lower() or "free tier" in txt.lower() \
                or "not available" in txt.lower() or "quota" in txt.lower():
            raise GenUnavailable(f"{r.status_code}: {txt}")
        r.raise_for_status()


def image(prompt: str, *, user_id: str, project_id: str, render_id: str | None, idem: str, aspect: str = "16:9",
          style: str = STYLE, refs: list[bytes] | None = None, ref_note: str = "") -> bytes:
    """One illustration (PNG/JPEG bytes). refs: reference images (e.g. the channel's main character) to keep."""
    ledger.ensure_within_cap(user_id, IMAGE_USD, "an AI illustration")
    t0 = time.time()
    parts: list[dict] = []
    for ref in refs or []:
        parts.append({"inlineData": {"mimeType": _mime(ref), "data": base64.b64encode(ref).decode()}})
    text = f"{prompt}. {style}"
    if refs:
        text = (f"{ref_note or 'Keep the character from the reference image exactly the same: same face, hair, body, clothes and colours.'} "
                f"New scene: {text}")
    parts.append({"text": text})
    r = requests.post(f"{API}/models/{IMAGE_MODEL}:generateContent", headers=_headers(), timeout=(20, 180), json={
        "contents": [{"parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect}},
    })
    _raise_for(r)
    for cand in r.json().get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            data = (part.get("inlineData") or part.get("inline_data") or {}).get("data")
            if data:
                ledger.record(user_id=user_id, project_id=project_id, render_id=render_id, stage="footage",
                              agent="illustrator", provider="gemini", model=IMAGE_MODEL, unit_type="requests",
                              units_out=1, cost_usd=IMAGE_USD, idempotency_key=idem,
                              duration_ms=int((time.time() - t0) * 1000), meta={"prompt": prompt[:300]})
                return base64.b64decode(data)
    raise RuntimeError("the image model returned no image (it may have declined the prompt)")


def _mime(b: bytes) -> str:
    return "image/png" if b[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def _find_video(obj):
    """Locate the video part anywhere in an Interactions response: inline base64 or a URI to download."""
    if isinstance(obj, dict):
        if obj.get("type") == "video" or "video" in str(obj.get("mime_type") or obj.get("mimeType") or ""):
            if obj.get("data"):
                return ("data", obj["data"])
            if obj.get("uri"):
                return ("uri", obj["uri"])
        for v in obj.values():
            hit = _find_video(v)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_video(v)
            if hit:
                return hit
    return None


def video(prompt: str, seconds: int, *, user_id: str, project_id: str, render_id: str | None, idem: str,
          first_frame: bytes | None = None, timeout_s: int = 600, aspect: str = "16:9", style: str = STYLE,
          motion: str = "Slow, smooth camera movement.") -> bytes:
    """A short clip (MP4 bytes), optionally starting from an illustration we already made."""
    seconds = max(3, min(15, int(seconds)))
    cost = VIDEO_USD_PER_S * seconds
    ledger.ensure_within_cap(user_id, cost, "an AI video clip")
    t0 = time.time()
    text = f"{prompt}. {style} {motion} No speech, no music, no text."
    inp: object = text
    if first_frame:
        inp = [{"type": "text", "text": text + " Start from this image."},
               {"type": "image", "data": base64.b64encode(first_frame).decode(), "mime_type": _mime(first_frame)}]
    r = requests.post(f"{API}/interactions", headers=_headers(), timeout=(20, 600), json={
        "model": VIDEO_MODEL, "input": inp,
        "response_format": {"type": "video", "duration": f"{seconds}s", "aspect_ratio": aspect, "resolution": "720p"},
    })
    _raise_for(r)
    body = r.json()
    # Long-running: poll the interaction until the video is there.
    while not _find_video(body) and str(body.get("status", "")).lower() in ("in_progress", "pending", "running", "queued"):
        if time.time() - t0 > timeout_s:
            raise TimeoutError("video generation took too long")
        time.sleep(8)
        g = requests.get(f"{API}/interactions/{body['id']}", headers=_headers(), timeout=60)
        _raise_for(g)
        body = g.json()
    hit = _find_video(body)
    if not hit:
        raise RuntimeError(f"no video in the response: {str(body)[:300]}")
    kind, val = hit
    if kind == "data":
        out = base64.b64decode(val)
    else:
        d = requests.get(val, headers={"x-goog-api-key": settings.gemini_api_key}, timeout=300)
        d.raise_for_status()
        out = d.content
    ledger.record(user_id=user_id, project_id=project_id, render_id=render_id, stage="footage", agent="illustrator",
                  provider="gemini", model=VIDEO_MODEL, unit_type="seconds", units_out=seconds, cost_usd=cost,
                  idempotency_key=idem, duration_ms=int((time.time() - t0) * 1000), meta={"prompt": prompt[:300]})
    return out
