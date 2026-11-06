"""Explainer animations (motion graphics) drawn frame by frame and piped into FFmpeg.

Everything here is made by the app: map imagery comes from public-domain US government services (NASA GIBS
Blue Marble relief worldwide, USGS aerial imagery for detail inside the US), all numbers come from the research
(the caller checks them), distances are computed, never guessed. Each function writes an H.264 MP4 of the
requested duration at 1920x1080, 30 fps.
"""
from __future__ import annotations

import math
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from ..config import settings
from .cards import SANS, SANS_B, _heavy, font
from .ff import FPS, H, W

ACC = (255, 205, 40)
WHITE = (255, 255, 255)
DARK = (8, 12, 20)
UA = {"User-Agent": "YouTubeChannelBuilder/1.0 (https://youtube-channel-builder.vercel.app)"}
MAP_CREDIT = "Imagery: NASA Blue Marble (GIBS) / USGS"


# ---------------------------------------------------------------- easing + output
def ease(t: float) -> float:  # ease in-out cubic
    t = max(0.0, min(1.0, t))
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def pop(t: float) -> float:
    """Overshoot pop-in 0 -> 1.12 -> 1 (pins, dots, labels)."""
    t = max(0.0, min(1.0, t))
    c = 1.70158 * 1.4
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


class Frames:
    """Pipe RGB frames to FFmpeg -> H.264 MP4."""

    def __init__(self, out: Path, seconds: float):
        self.out = Path(out)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.n = max(1, int(round(seconds * FPS)))
        self.tmp = self.out.with_suffix(".part.mp4")
        self.p = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
             "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(self.tmp)],
            stdin=subprocess.PIPE)

    def put(self, img: Image.Image) -> None:
        self.p.stdin.write(img.convert("RGB").tobytes())

    def close(self) -> Path:
        self.p.stdin.close()
        if self.p.wait() != 0:
            raise RuntimeError(f"ffmpeg failed writing {self.out.name}")
        self.tmp.replace(self.out)
        return self.out


def grid_bg(t: float = 0.0, accent=ACC) -> Image.Image:
    """Kept for compatibility: the blueprint backdrop at time t."""
    return Backdrop("blueprint", accent=accent).frame(t, 8.0)


# ---------------------------------------------------------------- backdrops (change from scene to scene)
PALETTES = {  # (base, glow 1, glow 2) per niche mood
    "true_crime": ((10, 10, 13), (110, 22, 26), (28, 38, 66)),
    "architecture": ((8, 13, 22), (28, 88, 150), (190, 130, 55)),
    "geopolitics": ((10, 13, 19), (140, 38, 36), (30, 68, 118)),
    "motorsport": ((12, 8, 8), (190, 28, 28), (60, 62, 72)),
    "science_space": ((4, 6, 18), (70, 34, 150), (18, 110, 170)),
    "business_tech": ((7, 14, 14), (18, 130, 105), (40, 58, 128)),
    "generic": ((10, 12, 18), (40, 80, 140), (150, 90, 58)),
}
STYLES = ("photo", "satellite", "aurora", "spotlight", "blueprint", "paper")
BW, BH = int(W * 1.14), int(H * 1.14)  # backdrops are drawn larger so they can drift


def _noise(w: int, h: int, seed: int, amount: int) -> Image.Image:
    import random
    rnd = random.Random(seed)
    small = Image.frombytes("L", (w // 2, h // 2), bytes(rnd.randrange(256) for _ in range((w // 2) * (h // 2))))
    return small.resize((w, h), Image.NEAREST).point(lambda v: 128 + (v - 128) * amount // 128)


def _vignette(img: Image.Image, strength: int = 200) -> Image.Image:
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).ellipse([-img.width * 0.15, -img.height * 0.2, img.width * 1.15, img.height * 1.2], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(img.width // 7))
    dark = Image.new("RGB", img.size, (0, 0, 0))
    return Image.composite(img, Image.blend(img, dark, strength / 255), m)


class Backdrop:
    """A moving background for an animation. Styles:
    photo     - the scene's own photo, blurred and darkened (so every scene looks different)
    satellite - darkened public-domain satellite view around the subject's location
    aurora    - soft colour glows in the niche's palette
    spotlight - dark stage with a warm light and film grain
    blueprint - drafting grid (engineering / architecture feel)
    paper     - dark archive paper texture (history / records feel)
    The image drifts slowly while the animation plays."""

    def __init__(self, style: str = "aurora", accent=ACC, niche: str = "generic", photo: Path | None = None,
                 latlon: tuple[float, float] | None = None, seed: int = 0):
        self.style, self.seed = style, seed
        base, g1, g2 = PALETTES.get(niche, PALETTES["generic"])
        img = None
        try:
            if style == "photo" and photo:
                from .cards import _cover
                im = _cover(Image.open(photo).convert("RGB"), BW, BH)
                im = im.filter(ImageFilter.GaussianBlur(16))
                im = ImageEnhance.Color(im).enhance(0.75)
                img = _vignette(ImageEnhance.Brightness(im).enhance(0.42), 170)
            elif style == "satellite" and latlon:
                cv = MapCanvas(latlon[0], latlon[1], 14.0, px=BW, detail=False)
                im = cv.img.resize((BW, BH), Image.LANCZOS).filter(ImageFilter.GaussianBlur(3))
                im = ImageEnhance.Color(im).enhance(0.7)
                img = _vignette(ImageEnhance.Brightness(im).enhance(0.5), 170)
                self.credit = MAP_CREDIT
        except Exception:  # noqa: BLE001 - a missing photo or tile never stops the video
            img = None
        if img is None:
            if style in ("photo", "satellite"):
                style = self.style = ("aurora", "spotlight", "paper")[seed % 3]
            img = Image.new("RGB", (BW, BH), base)
            if style == "aurora":
                glow = Image.new("RGB", (BW, BH), base)
                d = ImageDraw.Draw(glow)
                import random
                rnd = random.Random(seed)
                for col in (g1, g2, g1):
                    cx, cy, r = rnd.uniform(0.1, 0.9) * BW, rnd.uniform(0.1, 0.9) * BH, rnd.uniform(0.25, 0.45) * BW
                    d.ellipse([cx - r, cy - r * 0.7, cx + r, cy + r * 0.7], fill=col)
                img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(220)), 0.5)
                img = _vignette(img, 160)
            elif style == "spotlight":
                m = Image.new("L", (BW, BH), 0)
                cx = BW * (0.3 + 0.4 * ((seed * 37) % 10) / 10)
                ImageDraw.Draw(m).ellipse([cx - BW * 0.42, -BH * 0.3, cx + BW * 0.42, BH * 0.95], fill=150)
                warm = tuple(min(255, int(c * 0.5 + 40)) for c in g2)
                img = Image.composite(Image.new("RGB", (BW, BH), warm), img, m.filter(ImageFilter.GaussianBlur(200)))
            elif style == "blueprint":
                img = Image.new("RGB", (BW, BH), (10, 24, 46))
                d = ImageDraw.Draw(img)
                for x in range(0, BW, 48):
                    d.line([(x, 0), (x, BH)], fill=(40, 70, 110) if x % 240 == 0 else (22, 42, 72), width=2 if x % 240 == 0 else 1)
                for y in range(0, BH, 48):
                    d.line([(0, y), (BW, y)], fill=(40, 70, 110) if y % 240 == 0 else (22, 42, 72), width=2 if y % 240 == 0 else 1)
                img = _vignette(img, 150)
            elif style == "paper":
                img = Image.new("RGB", (BW, BH), (34, 30, 26))
                n = _noise(BW, BH, seed, 70).filter(ImageFilter.GaussianBlur(1.5))
                img = Image.composite(Image.new("RGB", (BW, BH), (66, 58, 48)), img, n)
                blot = _noise(BW // 8, BH // 8, seed + 3, 128).filter(ImageFilter.GaussianBlur(6)).resize((BW, BH), Image.BICUBIC)
                img = Image.composite(Image.new("RGB", (BW, BH), (24, 21, 18)), img, blot.point(lambda v: max(0, v - 140) * 2))
                img = _vignette(img, 190)
            grain = _noise(BW, BH, seed + 7, 18)
            img = Image.composite(Image.new("RGB", (BW, BH), (255, 255, 255)), img, grain.point(lambda v: max(0, v - 128) // 6))
        self.img = img
        self.credit = getattr(self, "credit", "")
        ang = (seed * 2.399) % (2 * math.pi)  # drift direction differs per backdrop
        self.dir = (math.cos(ang), math.sin(ang))

    def frame(self, t: float, seconds: float) -> Image.Image:
        k = ease(t / max(seconds, 0.1)) - 0.5
        x = (BW - W) / 2 + self.dir[0] * k * (BW - W) * 0.9
        y = (BH - H) / 2 + self.dir[1] * k * (BH - H) * 0.9
        return self.img.crop((int(x), int(y), int(x) + W, int(y) + H))

    def finish(self, d: ImageDraw.ImageDraw) -> None:
        if self.credit:
            _credit(d, self.credit)


def pick_style(scene_idx: int, k: int, niche: str, has_photo: bool, has_place: bool) -> str:
    """Rotate backdrop styles so consecutive animations never share a look. Real photos and satellite views come
    first when the scene has them; architecture leans on blueprints, true crime on paper/spotlight."""
    flavour = {"architecture": ["blueprint", "aurora"], "true_crime": ["paper", "spotlight"],
               "geopolitics": ["paper", "aurora"], "science_space": ["aurora", "spotlight"],
               "motorsport": ["spotlight", "aurora"], "business_tech": ["aurora", "spotlight"]}.get(niche, ["aurora", "spotlight"])
    order = (["photo"] if has_photo else []) + flavour + (["satellite"] if has_place else []) + \
        [x for x in ("aurora", "spotlight", "paper", "blueprint") if x not in flavour]
    return order[(scene_idx * 3 + k) % len(order)]


def _text(d, xy, s, f, fill=WHITE, anchor="la", stroke=3):
    d.text(xy, s, font=f, fill=fill, anchor=anchor, stroke_width=stroke, stroke_fill=(0, 0, 0))


def fmt_num(v: float) -> str:
    if abs(v) >= 100 or float(v).is_integer():
        return f"{int(round(v)):,}"
    return f"{v:,.1f}"


# ---------------------------------------------------------------- map imagery (public domain)
def _merc(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 256 * 2 ** z
    lat = max(-85.0, min(85.0, lat))
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def _in_us(lat: float, lon: float) -> bool:
    return 24 <= lat <= 50 and -125 <= lon <= -66


def _tile_url(z: int, x: int, y: int, us: bool) -> str:
    if us and z >= 9:
        return f"https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"
    return (f"https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/"
            f"GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg")


_tile_lock = threading.Lock()


def _tile(z: int, x: int, y: int, us: bool, depth: int = 0) -> Image.Image:
    """One map tile (cached). A tile that fails or comes back empty is rebuilt from its parent tile one zoom level
    up (upscaled quarter), down to NASA Blue Marble, so maps never show black squares."""
    import requests
    n = 2 ** z
    x %= n
    if not (0 <= y < n):
        return Image.new("RGB", (256, 256), (10, 20, 40))
    cache = settings.work_dir / "_tiles" / ("us" if us and z >= 9 else "bm") / str(z) / f"{x}_{y}.jpg"
    if cache.exists():
        return Image.open(cache).convert("RGB")
    im = None
    if not (z > 8 and not (us and z >= 9)):  # Blue Marble stops at zoom 8
        try:
            r = requests.get(_tile_url(z, x, y, us), headers=UA, timeout=30)
            if r.ok:
                im = Image.open(BytesIO(r.content)).convert("RGB")
                lo, hi = zip(*im.getextrema())
                if max(hi) < 25 or max(h - l for l, h in zip(lo, hi)) < 6:
                    im = None  # an empty "no data" tile
                else:  # USGS leaves the sea and edges pure black: fill those pixels from the coarser tile
                    nodata = im.convert("L").point(lambda v: 255 if v < 7 else 0).filter(ImageFilter.MaxFilter(3))
                    if nodata.getbbox():
                        parent = _tile(z - 1, x // 2, y // 2, us, depth + 1)
                        qx, qy = (x % 2) * 128, (y % 2) * 128
                        up = parent.crop((qx, qy, qx + 128, qy + 128)).resize((256, 256), Image.BICUBIC)
                        im = Image.composite(up, im, nodata.filter(ImageFilter.GaussianBlur(2)))
        except Exception:  # noqa: BLE001
            im = None
    if im is None:
        if z <= 1 or depth > 10:
            return Image.new("RGB", (256, 256), (10, 20, 40))
        parent = _tile(z - 1, x // 2, y // 2, us, depth + 1)
        qx, qy = (x % 2) * 128, (y % 2) * 128
        return parent.crop((qx, qy, qx + 128, qy + 128)).resize((256, 256), Image.BICUBIC)  # not cached: retried next time
    with _tile_lock:
        cache.parent.mkdir(parents=True, exist_ok=True)
    im.save(cache, quality=90)
    return im


class MapCanvas:
    """A stitched satellite/relief mosaic around a centre with helpers to project lat/lon into it."""

    def __init__(self, lat: float, lon: float, span_deg: float, px: int = 2600, detail: bool = True):
        us = _in_us(lat, lon) and detail
        zmax = 16 if us else 8
        z = int(max(2, min(zmax, round(math.log2(px / 256 * 360 / max(span_deg, 0.01))))))
        self.z, self.us = z, us
        cx, cy = _merc(lat, lon, z)
        w, h = px, int(px * H / W)
        x0, y0 = cx - w / 2, cy - h / 2
        tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int((x0 + w) // 256), int((y0 + h) // 256)
        mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
        jobs = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
        with ThreadPoolExecutor(8) as ex:
            for (tx, ty), im in zip(jobs, ex.map(lambda j: _tile(z, j[0], j[1], us), jobs)):
                mosaic.paste(im, ((tx - tx0) * 256, (ty - ty0) * 256))
        ox, oy = int(x0 - tx0 * 256), int(y0 - ty0 * 256)
        img = mosaic.crop((ox, oy, ox + w, oy + h))
        img = ImageEnhance.Contrast(img).enhance(1.15)
        img = ImageEnhance.Color(img).enhance(1.2)
        self.img = ImageEnhance.Brightness(img).enhance(0.82)
        self.x0, self.y0 = x0, y0

    def xy(self, lat: float, lon: float) -> tuple[float, float]:
        x, y = _merc(lat, lon, self.z)
        return x - self.x0, y - self.y0

    def view(self, zoom: float, cx: float | None = None, cy: float | None = None) -> tuple[Image.Image, callable]:
        """Frame at `zoom` (1 = whole canvas). Returns the frame and a mapper canvas-xy -> frame-xy."""
        cw, ch = self.img.size
        vw, vh = cw / zoom, ch / zoom
        cx = cw / 2 if cx is None else cx
        cy = ch / 2 if cy is None else cy
        left = max(0, min(cw - vw, cx - vw / 2))
        top = max(0, min(ch - vh, cy - vh / 2))
        frame = self.img.resize((W, H), Image.BILINEAR, box=(left, top, left + vw, top + vh))
        k = W / vw
        return frame, (lambda p: ((p[0] - left) * k, (p[1] - top) * k))


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _pin(d: ImageDraw.ImageDraw, x: float, y: float, s: float, color=(235, 60, 50)):
    if s <= 0:
        return
    r = 18 * s
    d.ellipse([x - r * 1.9, y - r * 1.9, x + r * 1.9, y + r * 1.9], outline=(*color,), width=max(1, int(3 * s)))
    d.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=WHITE, width=max(1, int(5 * s)))


def _label_box(d, x, y, text, t, f=None, above=True):
    """Typewriter label in a dark box (like map callouts in explainer channels)."""
    f = f or _heavy(54)
    n = int(len(text) * max(0.0, min(1.0, t)))
    if n <= 0:
        return
    s = text[:n]
    tw = d.textlength(text, font=f)
    bx = max(20, min(W - tw - 60, x - tw / 2 - 20))
    by = y - 60 - f.size - 30 if above else y + 50
    by = max(20, min(H - f.size - 60, by))
    d.rectangle([bx, by, bx + tw + 40, by + f.size + 30], fill=(0, 0, 0))
    d.text((bx + 20, by + 12), s, font=f, fill=WHITE)


def _dashed(d, p0, p1, frac, color=ACC, width=12, dash=34, gap=22):
    x0, y0 = p0
    x1, y1 = p1
    L = math.hypot(x1 - x0, y1 - y0)
    if L < 1:
        return
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    pos, end = 0.0, L * frac
    while pos < end:
        a = pos
        b = min(pos + dash, end)
        d.line([(x0 + ux * a, y0 + uy * a), (x0 + ux * b, y0 + uy * b)], fill=(0, 0, 0), width=width + 6)
        d.line([(x0 + ux * a, y0 + uy * a), (x0 + ux * b, y0 + uy * b)], fill=color, width=width)
        pos += dash + gap


def _credit(d, text=MAP_CREDIT):
    d.text((W - 24, H - 20), text, font=font(SANS, 20), fill=(220, 220, 220), anchor="rs")


# ---------------------------------------------------------------- animations
def route(out: Path, a: tuple[float, float, str], b: tuple[float, float, str], seconds: float = 8.0,
          accent=ACC, unit: str = "km") -> Path:
    """Satellite map; pins pop in, a dashed route draws itself, and the real distance counts up."""
    km = haversine_km(a[:2], b[:2])
    clat, clon = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    span = max(abs(a[1] - b[1]) * 2.2, abs(a[0] - b[0]) * 3.6, 0.05)
    cv = MapCanvas(clat, clon, span)
    pa, pb = cv.xy(a[0], a[1]), cv.xy(b[0], b[1])
    # Imagery outside the US stops at ~600 m/pixel: zoom the view so the route spans ~45% of the screen.
    dist_px = max(1.0, math.hypot(pa[0] - pb[0], pa[1] - pb[1]))
    z_base = max(1.0, min(7.0, 0.45 * cv.img.width / dist_px))
    mid = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
    vertical = abs(pa[1] - pb[1]) > abs(pa[0] - pb[0])
    a_above = pa[1] <= pb[1] if vertical else True
    b_above = (not a_above) if vertical else True
    fr = Frames(out, seconds)
    dist = f"{km:,.1f} {unit}" if km < 100 else f"{km:,.0f} {unit}"
    miles = f"{km * 0.621371:,.1f} MILES" if km < 100 else f"{km * 0.621371:,.0f} MILES"
    for i in range(fr.n):
        t = i / FPS
        frame, m = cv.view(z_base * (1.0 + 0.10 * ease(t / seconds)), *mid)
        d = ImageDraw.Draw(frame)
        A, B = m(pa), m(pb)
        _dashed(d, A, B, ease((t - 1.0) / 2.6), accent)
        _pin(d, *A, pop((t - 0.3) / 0.5))
        _pin(d, *B, pop((t - 3.4) / 0.5))
        _label_box(d, A[0], A[1], a[2].upper(), (t - 0.6) / 0.8, above=a_above)
        _label_box(d, B[0], B[1], b[2].upper(), (t - 3.7) / 0.8, above=b_above)
        k = ease((t - 3.8) / 1.4)
        if k > 0:
            # distance sits beside the line, on the side away from the labels
            mx, my = (A[0] + B[0]) / 2, (A[1] + B[1]) / 2
            dx, dy = B[0] - A[0], B[1] - A[1]
            L = max(1.0, math.hypot(dx, dy))
            nx, ny = -dy / L, dx / L
            if nx < 0:
                nx, ny = -nx, -ny
            px_, py_ = mx + nx * 230, my + ny * 230
            val = km * k
            s1 = (f"{val:,.1f}" if km < 100 else f"{val:,.0f}") + f" {unit}"
            big = _heavy(92)
            tw = d.textlength(dist, font=big)
            px_ = max(40 + tw / 2, min(W - 40 - tw / 2, px_))
            py_ = max(140, min(H - 140, py_))
            d.rounded_rectangle([px_ - tw / 2 - 30, py_ - 70, px_ + tw / 2 + 30, py_ + 95], radius=14, fill=(0, 0, 0))
            _text(d, (px_, py_ + 30), s1 if k < 1 else dist, big, fill=accent, anchor="mm", stroke=0)
            d.text((px_, py_ - 52), miles if k >= 1 else "", font=font(SANS_B, 34), fill=WHITE, anchor="mt")
        _credit(d)
        fr.put(frame)
    return fr.close()


def place(out: Path, lat: float, lon: float, label: str, seconds: float = 6.0, sub: str = "", accent=ACC) -> Path:
    """Camera zooms from the region into the place; a pin drops and the name types itself out."""
    wide = MapCanvas(lat, lon, 40.0)
    close = MapCanvas(lat, lon, 3.0 if not _in_us(lat, lon) else 0.6)
    fr = Frames(out, seconds)
    for i in range(fr.n):
        t = i / FPS
        k = ease(t / (seconds * 0.6))
        # wide canvas zooms 1x -> ~13x; past 60% of the move, crossfade into the detailed canvas
        zw = 1 + 11 * k
        f1, m1 = wide.view(zw, *wide.xy(lat, lon))
        if k > 0.55:
            zc = 1 + 0.25 * ease((t - seconds * 0.35) / (seconds * 0.65))
            f2, m2 = close.view(zc, *close.xy(lat, lon))
            frame = Image.blend(f1, f2, min(1.0, (k - 0.55) / 0.3))
            P = m2(close.xy(lat, lon))
        else:
            frame = f1
            P = m1(wide.xy(lat, lon))
        d = ImageDraw.Draw(frame)
        tp = (t - seconds * 0.55) / 0.5
        _pin(d, P[0], P[1] - 30 * (1 - ease_out(tp)) if tp < 1 else P[1], pop(tp))
        _label_box(d, P[0], P[1], label.upper(), (t - seconds * 0.62) / 0.9, f=_heavy(72))
        if sub:
            a = ease((t - seconds * 0.8) / 0.5)
            if a > 0:
                _text(d, (P[0], P[1] + 70), sub, font(SANS_B, 40), fill=accent, anchor="mt")
        _credit(d)
        fr.put(frame)
    return fr.close()


def counter(out: Path, value: float, unit: str, label: str, seconds: float = 5.0, accent=ACC, prefix: str = "",
            bg: Backdrop | None = None) -> Path:
    """A key figure counts up over a drifting grid, then the label slides in under an animated underline."""
    bg = bg or Backdrop("aurora", accent)
    fr = Frames(out, seconds)
    big = _heavy(220)
    for i in range(fr.n):
        t = i / FPS
        img = bg.frame(t, seconds)
        d = ImageDraw.Draw(img)
        k = ease_out(t / (seconds * 0.45))
        s = prefix + fmt_num(value * k if k < 1 else value)
        _text(d, (W / 2, H * 0.47), s, big, fill=WHITE, anchor="mb", stroke=8)
        if unit:
            _text(d, (W / 2, H * 0.47 + 10), unit.upper(), _heavy(80), fill=accent, anchor="mt", stroke=5)
        u = ease((t - seconds * 0.35) / 0.6)
        if u > 0:
            half = 360 * u
            d.rectangle([W / 2 - half, H * 0.72, W / 2 + half, H * 0.72 + 8], fill=accent)
        a = ease((t - seconds * 0.45) / 0.6)
        if a > 0:
            y = H * 0.76 + 30 * (1 - a)
            lab = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(lab).text((W / 2, y), label, font=font(SANS_B, 54), fill=(255, 255, 255, int(255 * a)), anchor="mt")
            img.paste(lab, (0, 0), lab)
        bg.finish(ImageDraw.Draw(img))
        fr.put(img)
    return fr.close()


def bars(out: Path, title: str, labels: list[str], values: list[float], unit: str = "", seconds: float = 7.0,
         accent=ACC, highlight: int | None = None, bg: Backdrop | None = None) -> Path:
    """Comparison bars grow one after another while their numbers count up."""
    bg = bg or Backdrop("aurora", accent)
    fr = Frames(out, seconds)
    n = len(values)
    vmax = max(max(values), 1e-9)
    hi = values.index(max(values)) if highlight is None else highlight
    top, bottom = H * 0.24, H * 0.9
    row = (bottom - top) / n
    bh = min(84, row * 0.62)
    lx, x0, x1 = 120, 120 + 470, W - 300
    for i in range(fr.n):
        t = i / FPS
        img = bg.frame(t, seconds)
        d = ImageDraw.Draw(img)
        a = ease(t / 0.6)
        _text(d, (120, H * 0.1 + 20 * (1 - a)), title.upper(), _heavy(60), fill=WHITE, stroke=4)
        if unit:
            d.text((122, H * 0.1 + 80), unit, font=font(SANS, 34), fill=(190, 200, 215))
        for j, (lab, v) in enumerate(zip(labels, values)):
            k = ease_out((t - 0.5 - j * 0.45) / 1.3)
            y = top + row * j + (row - bh) / 2
            if k <= 0:
                continue
            d.text((lx, y + bh / 2), lab[:24], font=font(SANS_B, 40), fill=WHITE, anchor="lm")
            w = max(6, (x1 - x0) * v / vmax * k)
            col = accent if j == hi else (120, 140, 170)
            d.rounded_rectangle([x0, y, x0 + w, y + bh], radius=10, fill=col)
            _text(d, (x0 + w + 18, y + bh / 2), fmt_num(v * k if k < 1 else v), _heavy(46), fill=WHITE, anchor="lm", stroke=3)
        bg.finish(ImageDraw.Draw(img))
        fr.put(img)
    return fr.close()


def timeline(out: Path, items: list[tuple[str, str]], seconds: float = 8.0, accent=ACC, title: str = "",
             bg: Backdrop | None = None) -> Path:
    """A line draws across the screen; dated events pop in one by one, alternating above and below."""
    items = items[:6]
    bg = bg or Backdrop("paper", accent)
    fr = Frames(out, seconds)
    n = len(items)
    y0 = H * 0.55
    xs = [200 + (W - 400) * (j / max(1, n - 1)) for j in range(n)] if n > 1 else [W / 2]
    for i in range(fr.n):
        t = i / FPS
        img = bg.frame(t, seconds)
        d = ImageDraw.Draw(img)
        if title:
            _text(d, (W / 2, H * 0.12), title.upper(), _heavy(58), anchor="mt", stroke=4)
        k = ease(t / (seconds * 0.7))
        d.line([(140, y0), (140 + (W - 280) * k, y0)], fill=(200, 210, 225), width=6)
        for j, (date, text) in enumerate(items):
            p = pop((t - 0.4 - j * (seconds * 0.7 / max(1, n))) / 0.45)
            if p <= 0:
                continue
            x = xs[j]
            r = 16 * p
            d.ellipse([x - r, y0 - r, x + r, y0 + r], fill=accent, outline=WHITE, width=4)
            up = j % 2 == 0
            ya = y0 - 70 if up else y0 + 60
            _text(d, (x, ya), date, _heavy(44), fill=accent, anchor="mb" if up else "mt", stroke=3)
            words, lines, cur = text.split(), [], ""
            for w_ in words:
                if len(cur + " " + w_) > 20 and cur:
                    lines.append(cur)
                    cur = w_
                else:
                    cur = (cur + " " + w_).strip()
            lines.append(cur)
            for li, ln in enumerate(lines[:3]):
                yy = (ya - 60 - (len(lines[:3]) - 1 - li) * 40) if up else (ya + 60 + li * 40)
                d.text((x, yy), ln, font=font(SANS, 32), fill=WHITE, anchor="mb" if up else "mt")
        bg.finish(ImageDraw.Draw(img))
        fr.put(img)
    return fr.close()


def framed_photo(out: Path, photo: Path, seconds: float = 6.0, caption: str = "", accent=ACC, tilt: float = -3.0,
                 bg: Backdrop | None = None) -> Path:
    """A photo as a framed print over the moving grid: it slides in, settles with a slight tilt, slowly zooms."""
    src = Image.open(photo).convert("RGB")
    src = ImageEnhance.Contrast(src).enhance(1.08)
    mw, mh = int(W * 0.62), int(H * 0.66)
    k = min(mw / src.width, mh / src.height)
    pw, ph = int(src.width * k), int(src.height * k)
    base = src.resize((pw, ph), Image.LANCZOS)
    card = Image.new("RGB", (pw + 28, ph + 28), (245, 245, 240))
    card.paste(base, (14, 14))
    bg = bg or Backdrop("photo", accent, photo=photo)
    fr = Frames(out, seconds)
    for i in range(fr.n):
        t = i / FPS
        img = bg.frame(t, seconds)
        a = ease_out(t / 0.9)
        z = 1 + 0.05 * (t / seconds)
        c = card.resize((int(card.width * z), int(card.height * z)), Image.BILINEAR)
        c = c.convert("RGBA").rotate(tilt * a, expand=True, resample=Image.BICUBIC)
        sh = Image.new("RGBA", c.size, (0, 0, 0, 0))
        sh.paste((0, 0, 0, 150), (0, 0), c.split()[3])
        sh = sh.filter(ImageFilter.GaussianBlur(18))
        x = int((W - c.width) / 2)
        y = int((H - c.height) / 2 + 220 * (1 - a)) - (30 if caption else 0)
        img.paste(sh, (x + 18, y + 24), sh)
        img.paste(c, (x, y), c)
        if caption:
            al = ease((t - 0.8) / 0.6)
            if al > 0:
                d = ImageDraw.Draw(img)
                _text(d, (W / 2, y + c.height + 26), caption, font(SANS_B, 40), fill=WHITE, anchor="mt", stroke=3)
        bg.finish(ImageDraw.Draw(img))
        fr.put(img)
    return fr.close()


def _wrap(d, text: str, f, max_w: float) -> list[str]:
    lines, cur = [], ""
    for w_ in text.split():
        t = (cur + " " + w_).strip()
        if cur and d.textlength(t, font=f) > max_w:
            lines.append(cur)
            cur = w_
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def _fade_text(ld: ImageDraw.ImageDraw, xy, s: str, f, fill, a: float, anchor="la", stroke=0):
    """Draw text with opacity a (0..1) onto a shared RGBA layer (pasted once per frame)."""
    if a <= 0:
        return
    ld.text(xy, s, font=f, fill=(*fill, int(255 * min(1.0, a))), anchor=anchor,
            stroke_width=stroke, stroke_fill=(0, 0, 0, int(200 * min(1.0, a))))


def _layer():
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    return lay, ImageDraw.Draw(lay)


def title_card(out: Path, number: int, title: str, kicker: str = "", seconds: float = 5.0, accent=ACC,
               bg: Backdrop | None = None) -> Path:
    """Chapter opener: 'CHAPTER 3' slides in, the title rises word by word, an accent line draws under it."""
    bg = bg or Backdrop("aurora", accent, seed=number)
    fr = Frames(out, seconds)
    big = _heavy(110)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = _wrap(probe, title.upper(), big, W * 0.78)[:3]
    words = [(li, w_) for li, ln in enumerate(lines) for w_ in ln.split()]
    lh = 124
    y0 = H / 2 - len(lines) * lh / 2 + 30
    for i in range(fr.n):
        t = i / FPS
        img = bg.frame(t, seconds)
        d = ImageDraw.Draw(img)
        lay, ld = _layer()
        a = ease(t / 0.6)
        _fade_text(ld, (W * 0.11 - 60 * (1 - a), y0 - 90), f"CHAPTER {number}", _heavy(46), accent, a)
        xs: dict[int, float] = {}
        for j, (li, w_) in enumerate(words):
            k = ease_out((t - 0.35 - j * 0.12) / 0.5)
            x = xs.get(li, W * 0.11)
            _fade_text(ld, (x, y0 + li * lh + 40 * (1 - k)), w_, big, WHITE, k, stroke=2)
            xs[li] = x + d.textlength(w_ + " ", font=big)
        u = ease((t - 0.9) / 0.8)
        if u > 0:
            yl = y0 + len(lines) * lh + 18
            d.rectangle([W * 0.11, yl, W * 0.11 + 420 * u, yl + 10], fill=accent)
        if kicker:
            _fade_text(ld, (W * 0.11, y0 + len(lines) * lh + 56), kicker, font(SANS_B, 38), (215, 220, 228),
                       ease((t - 1.3) / 0.6))
        img.paste(lay, (0, 0), lay)
        bg.finish(ImageDraw.Draw(img))
        fr.put(img)
    return fr.close()


def quote(out: Path, text: str, label: str = "", seconds: float = 7.0, accent=ACC, bg: Backdrop | None = None) -> Path:
    """A key line from the narration builds word by word; numbers light up in the accent colour."""
    import re as _re
    bg = bg or Backdrop("spotlight", accent)
    fr = Frames(out, seconds)
    f = _heavy(76 if len(text) < 110 else 62)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = _wrap(probe, text, f, W * 0.72)[:5]
    lh = int(f.size * 1.22)
    y0 = H / 2 - len(lines) * lh / 2
    words = [(li, w_) for li, ln in enumerate(lines) for w_ in ln.split()]
    span = seconds * 0.55
    for i in range(fr.n):
        t = i / FPS
        img = bg.frame(t, seconds)
        d = ImageDraw.Draw(img)
        lay, ld = _layer()
        bar = ease(t / 0.5)
        d.rectangle([W * 0.12, y0 - 10, W * 0.12 + 12, y0 - 10 + (len(lines) * lh + 10) * bar], fill=accent)
        if label:
            _fade_text(ld, (W * 0.12 + 44, y0 - 70), label.upper(), font(SANS_B, 34), accent, ease((t - 0.2) / 0.5))
        xs: dict[int, float] = {}
        for j, (li, w_) in enumerate(words):
            k = ease_out((t - 0.3 - j * span / max(1, len(words))) / 0.35)
            x = xs.get(li, W * 0.12 + 44)
            hot = bool(_re.search(r"\d", w_))
            _fade_text(ld, (x, y0 + li * lh + 18 * (1 - k)), w_, f, accent if hot else WHITE, k, stroke=2)
            xs[li] = x + d.textlength(w_ + " ", font=f)
        img.paste(lay, (0, 0), lay)
        bg.finish(ImageDraw.Draw(img))
        fr.put(img)
    return fr.close()


def points(out: Path, title: str, items: list[str], seconds: float = 8.0, accent=ACC, bg: Backdrop | None = None) -> Path:
    """Key points slide in one by one, each with a numbered accent tile."""
    items = items[:5]
    bg = bg or Backdrop("aurora", accent)
    fr = Frames(out, seconds)
    f = font(SANS_B, 50)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    wrapped = [_wrap(probe, it, f, W * 0.66)[:2] for it in items]
    row = min(170, (H * 0.68) / max(1, len(items)))
    top = H * 0.27
    for i in range(fr.n):
        t = i / FPS
        img = bg.frame(t, seconds)
        d = ImageDraw.Draw(img)
        lay, ld = _layer()
        a = ease(t / 0.6)
        _fade_text(ld, (W * 0.12, H * 0.12 + 20 * (1 - a)), title.upper(), _heavy(64), WHITE, a, stroke=2)
        for j, lines in enumerate(wrapped):
            k = ease_out((t - 0.6 - j * (seconds * 0.55 / max(1, len(items)))) / 0.5)
            if k <= 0:
                continue
            x = W * 0.12 - 80 * (1 - k)
            y = top + j * row
            d.rounded_rectangle([x, y, x + 76, y + 76], radius=12, fill=accent)
            d.text((x + 38, y + 38), str(j + 1), font=_heavy(48), fill=(10, 10, 10), anchor="mm")
            for li, ln in enumerate(lines):
                _fade_text(ld, (x + 110, y + 8 + li * 58), ln, f, WHITE, k, stroke=2)
        img.paste(lay, (0, 0), lay)
        bg.finish(ImageDraw.Draw(img))
        fr.put(img)
    return fr.close()
