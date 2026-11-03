"""Location maps drawn from Natural Earth (public domain) data, in the documentary card style.

geocode(): OpenStreetMap Nominatim (1 request/second, attribution in credits), falling back to the offline
Natural Earth populated-places list. Maps are tier 'generated' graphics, never presented as footage.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .cards import ACCENT, BG, INK, MUTED, SANS, SANS_B, _grain, _save, _vignette, font

DATA = Path(os.environ.get("MAPDATA_DIR", "/models/naturalearth"))
LAND = (30, 35, 44)
LAND_HI = (52, 58, 70)
BORDER = (74, 80, 92)
WATER = BG
_nom_lock = threading.Lock()
_nom_last = [0.0]


def _load(name: str) -> dict:
    p = DATA / f"{name}.geojson"
    return json.loads(p.read_text()) if p.exists() else {"features": []}


@lru_cache(maxsize=1)
def _states() -> list[dict]:
    return _load("ne_50m_admin_1_states_provinces_lakes")["features"]


@lru_cache(maxsize=1)
def _countries() -> list[dict]:
    return _load("ne_50m_admin_0_countries")["features"]


@lru_cache(maxsize=1)
def _places() -> list[dict]:
    return _load("ne_10m_populated_places_simple")["features"]


def available() -> bool:
    return (DATA / "ne_50m_admin_0_countries.geojson").exists()


# ---------------------------------------------------------------- geocoding
def geocode(place: str) -> tuple[float, float, str] | None:
    """(lat, lon, label) for 'City, State' or None. Never guesses: no match -> None."""
    place = (place or "").strip()
    if not place:
        return None
    try:
        import requests
        with _nom_lock:
            wait = 1.1 - (time.time() - _nom_last[0])
            if wait > 0:
                time.sleep(wait)
            r = requests.get("https://nominatim.openstreetmap.org/search", timeout=20,
                             params={"q": place, "format": "json", "limit": 1, "addressdetails": 0},
                             headers={"User-Agent": "YouTubeChannelBuilder/1.0 (https://youtube-channel-builder.vercel.app)"})
            _nom_last[0] = time.time()
        if r.ok and r.json():
            h = r.json()[0]
            return float(h["lat"]), float(h["lon"]), place
    except Exception:  # noqa: BLE001 - fall back to offline data
        pass
    city = re.split(r",", place)[0].strip().lower()
    for f in _places():
        pr = f["properties"]
        if (pr.get("name") or "").lower() == city:
            return float(pr["latitude"]), float(pr["longitude"]), place
    return None


# ---------------------------------------------------------------- drawing
def _rings(geom: dict):
    if geom["type"] == "Polygon":
        yield from geom["coordinates"]
    elif geom["type"] == "MultiPolygon":
        for poly in geom["coordinates"]:
            yield from poly


def _contains(geom: dict, lon: float, lat: float) -> bool:
    def inside(ring):
        c = False
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1:
                c = not c
        return c
    if geom["type"] == "Polygon":
        return inside(geom["coordinates"][0])
    return any(inside(p[0]) for p in geom["coordinates"])


def _bbox(geom: dict) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for r in _rings(geom):
        for x, y in r:
            xs.append(x)
            ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def region_of(lat: float, lon: float) -> dict | None:
    for f in _states():
        if _contains(f["geometry"], lon, lat):
            return f
    for f in _countries():
        if _contains(f["geometry"], lon, lat):
            return f
    return None


def location_map(out: Path, place: str, kicker: str = "LOCATION", seed: int = 0, latlon: tuple[float, float] | None = None) -> Path | None:
    """A dark map centred on the region (state/province) with a pin and label. None if the place is unknown."""
    if not available():
        return None
    g = (latlon[0], latlon[1], place) if latlon else geocode(place)
    if not g:
        return None
    lat, lon, label = g
    reg = region_of(lat, lon)
    if reg:
        x0, y0, x1, y1 = _bbox(reg["geometry"])
        pad_x, pad_y = (x1 - x0) * 0.9 + 1.5, (y1 - y0) * 0.9 + 1.0
    else:
        x0 = x1 = lon
        y0 = y1 = lat
        pad_x, pad_y = 6, 4
    W, H = 2112, 1188  # 10% oversize for the slow pan
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    kx = math.cos(math.radians(cy))
    span_x = max((x1 - x0) + pad_x, 1.0) * kx
    span_y = max((y1 - y0) + pad_y, 1.0)
    scale = min(W * 0.9 / span_x, H * 0.85 / span_y)

    def P(x, y):  # equirectangular, latitude-corrected
        return (W / 2 + (x - cx) * kx * scale, H / 2 - (y - cy) * scale)

    img = Image.new("RGB", (W, H), WATER)
    d = ImageDraw.Draw(img)
    vx0, vx1 = cx - W / 2 / (kx * scale), cx + W / 2 / (kx * scale)
    vy0, vy1 = cy - H / 2 / scale, cy + H / 2 / scale

    def visible(geom):
        a, b, c, e = _bbox(geom)
        return not (c < vx0 or a > vx1 or e < vy0 or b > vy1)

    for f in _countries():
        if visible(f["geometry"]):
            for r in _rings(f["geometry"]):
                if len(r) > 2:
                    d.polygon([P(x, y) for x, y in r], fill=LAND)
    for f in _states():
        if visible(f["geometry"]):
            hi = reg is not None and f is reg
            for r in _rings(f["geometry"]):
                if len(r) > 2:
                    d.polygon([P(x, y) for x, y in r], fill=LAND_HI if hi else LAND, outline=BORDER)
    if reg:
        for r in _rings(reg["geometry"]):
            d.line([P(x, y) for x, y in r] + [P(*r[0])], fill=ACCENT, width=3)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    d = ImageDraw.Draw(img)
    px, py = P(lon, lat)
    for rad, a in ((46, 40), (30, 80)):
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([px - rad, py - rad, px + rad, py + rad], fill=(*ACCENT, a))
        img.paste(glow, (0, 0), glow)
    d.ellipse([px - 11, py - 11, px + 11, py + 11], fill=ACCENT, outline=INK, width=3)
    f_label = font(SANS_B, 46)
    tx = px + 30 if px < W * 0.7 else px - 30 - d.textlength(label, font=f_label)
    d.text((tx + 2, py - 30 + 2), label, font=f_label, fill=(0, 0, 0))
    d.text((tx, py - 30), label, font=f_label, fill=INK)
    d.text((W * 0.08, H * 0.1), kicker, font=font(SANS_B, 32), fill=ACCENT)
    region_name = (reg or {}).get("properties", {}).get("name") or ""
    if region_name:
        d.text((W * 0.08, H * 0.1 + 48), region_name, font=font(SANS, 34), fill=MUTED)
    d.text((W * 0.08, H * 0.9), "Map data: Natural Earth · Geocoding © OpenStreetMap contributors", font=font(SANS, 22), fill=MUTED)
    return _save(_vignette(_grain(img, seed)), out)
