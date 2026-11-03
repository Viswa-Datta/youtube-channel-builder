"""App-generated visuals: chapter cards, 'document on camera' cards, timeline cards, thumbnails.
These are tier 'generated' (made for the video), never presented as real footage.
"""
from __future__ import annotations

import random
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIRS = ["/usr/share/fonts/opentype/inter", "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/inter"]


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for d in FONT_DIRS:
        p = Path(d) / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    fallbacks = {"serif": "DejaVuSerif.ttf", "serif-bold": "DejaVuSerif-Bold.ttf", "sans": "Inter-Regular.otf",
                 "sans-bold": "Inter-SemiBold.otf", "mono": "DejaVuSansMono.ttf"}
    for d in FONT_DIRS:
        for f in fallbacks.values():
            if (Path(d) / f).exists():
                return ImageFont.truetype(str(Path(d) / f), size)
    return ImageFont.load_default()


SERIF = "DejaVuSerif.ttf"
SERIF_B = "DejaVuSerif-Bold.ttf"
SANS = "Inter-Regular.otf"
SANS_B = "Inter-SemiBold.otf"
MONO = "DejaVuSansMono.ttf"

BG = (17, 20, 26)
INK = (232, 228, 218)
MUTED = (150, 150, 145)
ACCENT = (196, 160, 92)
PAPER = (236, 231, 218)


def _grain(img: Image.Image, seed: int, amount: int = 10) -> Image.Image:
    rnd = random.Random(seed)
    noise = Image.effect_noise(img.size, amount + rnd.randint(0, 4)).convert("L")
    return Image.blend(img, Image.merge("RGB", (noise, noise, noise)), 0.035)


def _vignette(img: Image.Image) -> Image.Image:
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse([-w * 0.15, -h * 0.25, w * 1.15, h * 1.25], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(220))
    dark = Image.new("RGB", (w, h), (6, 7, 9))
    return Image.composite(img, dark, mask)


def canvas(scale: float = 1.1) -> Image.Image:
    return Image.new("RGB", (int(1920 * scale), int(1080 * scale)), BG)


def chapter_card(out: Path, number: int, title: str, kicker: str, seed: int = 0) -> Path:
    img = canvas()
    d = ImageDraw.Draw(img)
    W, H = img.size
    d.text((W * 0.12, H * 0.36), f"CHAPTER {number}", font=font(SANS_B, 34), fill=ACCENT, spacing=8)
    d.line([(W * 0.12, H * 0.43), (W * 0.12 + 140, H * 0.43)], fill=ACCENT, width=3)
    y = H * 0.47
    for line in textwrap.wrap(title, 30):
        d.text((W * 0.12, y), line, font=font(SERIF_B, 92), fill=INK)
        y += 112
    d.text((W * 0.12, y + 24), kicker, font=font(SANS, 34), fill=MUTED)
    return _save(_vignette(_grain(img, seed)), out)


def document_card(out: Path, heading: str, lines: list[str], footer: str, seed: int = 0) -> Path:
    """A typed 'case file' page on a desk: for dates, record names, and short quotes from records."""
    img = canvas()
    W, H = img.size
    rnd = random.Random(seed)
    page = Image.new("RGB", (int(W * 0.56), int(H * 0.54)), PAPER)
    pd = ImageDraw.Draw(page)
    pw, ph = page.size
    pd.rectangle([0, 0, pw - 1, ph - 1], outline=(200, 194, 178), width=3)
    pd.text((70, 70), heading.upper(), font=font(MONO, 34), fill=(40, 40, 40))
    pd.line([(70, 125), (pw - 70, 125)], fill=(120, 110, 95), width=2)
    y = 165
    for ln in lines:
        for w in textwrap.wrap(ln, 46):
            pd.text((70, y), w, font=font(MONO, 30), fill=(35, 35, 35))
            y += 44
        y += 22
        if y > ph - 140:
            break
    pd.text((70, ph - 90), footer, font=font(MONO, 22), fill=(110, 100, 90))
    angle = rnd.uniform(-2.2, 2.2)
    alpha = Image.new("L", page.size, 255).rotate(angle, expand=True, resample=Image.BICUBIC)
    page = page.rotate(angle, expand=True, resample=Image.BICUBIC)
    x, y0 = int(W * 0.22 + rnd.uniform(-20, 20)), int(H * 0.08)
    shadow_mask = Image.new("L", img.size, 0)
    shadow_mask.paste(alpha, (x + 26, y0 + 34))
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(28)).point(lambda v: int(v * 0.7))
    img = Image.composite(Image.new("RGB", img.size, (0, 0, 0)), img, shadow_mask)
    img.paste(page, (x, y0), mask=alpha)
    return _save(_vignette(_grain(img, seed)), out)


def timeline_card(out: Path, items: list[tuple[str, str]], highlight: int, title: str, seed: int = 0) -> Path:
    img = canvas()
    d = ImageDraw.Draw(img)
    W, H = img.size
    d.text((W * 0.1, H * 0.12), title.upper(), font=font(SANS_B, 32), fill=ACCENT)
    y0, x0, x1 = H * 0.42, W * 0.1, W * 0.9
    d.line([(x0, y0), (x1, y0)], fill=(90, 90, 90), width=4)
    n = max(1, len(items))
    for i, (when, what) in enumerate(items):
        x = x0 + (x1 - x0) * (i + 0.5) / n
        on = i == highlight
        r = 16 if on else 10
        d.ellipse([x - r, y0 - r, x + r, y0 + r], fill=ACCENT if on else (120, 120, 120))
        d.text((x, y0 - 70), when, font=font(SANS_B, 34 if on else 28), fill=INK if on else MUTED, anchor="mm")
        yy = y0 + 60
        for w in textwrap.wrap(what, 18)[:4]:
            d.text((x, yy), w, font=font(SANS, 28 if on else 24), fill=INK if on else MUTED, anchor="mm")
            yy += 38
    return _save(_vignette(_grain(img, seed)), out)


def statement_card(out: Path, text: str, label: str, seed: int = 0) -> Path:
    img = canvas()
    d = ImageDraw.Draw(img)
    W, H = img.size
    d.text((W * 0.14, H * 0.2), label.upper(), font=font(SANS_B, 30), fill=ACCENT)
    y = H * 0.27
    lines = textwrap.wrap(text, 42)
    if len(lines) > 4:
        lines = lines[:4]
        lines[-1] = lines[-1].rstrip(".,;") + "…"
    for line in lines:
        d.text((W * 0.14, y), line, font=font(SERIF, 58), fill=INK)
        y += 80
    return _save(_vignette(_grain(img, seed)), out)


def thumbnail(out: Path, text: str, sub: str) -> Path:
    """Text-led thumbnail. Never built from a video frame, so it can never be a graphic frame."""
    img = Image.new("RGB", (1280, 720), (14, 16, 21))
    d = ImageDraw.Draw(img)
    for i in range(0, 1280, 4):  # subtle archival stripes
        d.line([(i, 0), (i, 720)], fill=(18, 20, 26) if i % 8 else (16, 18, 23))
    d.rectangle([60, 70, 72, 650], fill=ACCENT)
    # headline: the largest size whose wrapped lines (max 3) all fit left of the folder graphic
    best = None
    for size in range(132, 58, -6):
        f = font(SANS_B, size)
        for width in range(6, 20):
            lines = textwrap.wrap(text.upper(), width)
            if len(lines) <= 3 and all(d.textlength(l, font=f) <= 740 for l in lines):
                best = (size, f, lines)
                break
        if best:
            break
    size, f, lines = best or (60, font(SANS_B, 60), textwrap.wrap(text.upper(), 18)[:3])
    y = 120
    for line in lines:
        d.text((110, y), line, font=f, fill=INK)
        y += int(size * 1.18)
    d.text((112, y + 20), sub, font=font(SANS, 44), fill=ACCENT)
    folder = Image.new("RGB", (330, 420), PAPER)
    fd = ImageDraw.Draw(folder)
    fd.text((30, 40), "CASE FILE", font=font(MONO, 34), fill=(50, 50, 50))
    for k in range(6):
        fd.line([(30, 120 + k * 45), (300, 120 + k * 45)], fill=(150, 140, 125), width=3)
    folder = folder.rotate(-7, expand=True, fillcolor=(14, 16, 21))
    img.paste(folder, (880, 160))
    return _save(_vignette(img), out, quality=92)


def _save(img: Image.Image, out: Path, quality: int = 90) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=quality) if out.suffix.lower() in (".jpg", ".jpeg") else img.save(out)
    return out
