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


def chart_card(out: Path, title: str, labels: list[str], values: list[float], unit: str = "", note: str = "", seed: int = 0) -> Path:
    """Horizontal bar chart in the card style; the largest bar is highlighted. Values are drawn exactly as sourced."""
    img = canvas()
    d = ImageDraw.Draw(img)
    W, H = img.size
    d.text((W * 0.1, H * 0.1), title.upper(), font=font(SANS_B, 34), fill=ACCENT)
    if unit:
        d.text((W * 0.1, H * 0.1 + 52), unit, font=font(SANS, 28), fill=MUTED)
    n = len(values)
    top, bottom = H * 0.26, H * 0.86
    row = (bottom - top) / n
    bar_h = min(70, row * 0.62)
    label_w = W * 0.26
    x0, x1 = W * 0.1 + label_w, W * 0.86
    vmax = max(max(values), 1e-9)
    hi = values.index(max(values))

    def fmt(v: float) -> str:
        return f"{int(v):,}" if float(v).is_integer() else f"{v:,.2f}".rstrip("0").rstrip(".")
    for i, (lab, v) in enumerate(zip(labels, values)):
        y = top + row * i + (row - bar_h) / 2
        d.text((W * 0.1, y + bar_h / 2), textwrap.shorten(lab, 26, placeholder="…"), font=font(SANS, 30), fill=INK, anchor="lm")
        w = max(4, (x1 - x0) * (v / vmax)) if v > 0 else 4
        d.rectangle([x0, y, x0 + w, y + bar_h], fill=ACCENT if i == hi else (92, 98, 110))
        d.text((x0 + w + 16, y + bar_h / 2), fmt(v), font=font(SANS_B, 30), fill=INK, anchor="lm")
    if note:
        d.text((W * 0.1, H * 0.92), note, font=font(SANS, 22), fill=MUTED)
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


SANS_BLACK = "Inter-Black.otf"
THUMB_ACCENTS = {  # highlight colour per niche preset: bright, high-contrast, readable at phone size
    "true_crime": (255, 70, 60), "architecture": (255, 205, 40), "geopolitics": (255, 120, 40),
    "motorsport": (255, 40, 60), "science_space": (60, 210, 255), "business_tech": (60, 230, 140), "generic": (255, 205, 40),
}


def _heavy(size: int) -> ImageFont.FreeTypeFont:
    try:
        return font(SANS_BLACK, size)
    except Exception:  # noqa: BLE001
        return font(SANS_B, size)


def _cover(im: Image.Image, w: int, h: int, focus_x: float = 0.5, focus_y: float = 0.42) -> Image.Image:
    """Scale to cover w x h and crop around a focus point (buildings and people sit slightly above centre)."""
    im = im.convert("RGB")
    k = max(w / im.width, h / im.height)
    im = im.resize((max(w, round(im.width * k)), max(h, round(im.height * k))), Image.LANCZOS)
    x = int((im.width - w) * focus_x)
    y = int((im.height - h) * focus_y)
    return im.crop((x, y, x + w, y + h))


def _grade(im: Image.Image) -> Image.Image:
    """Punchy, high-contrast grade that survives YouTube's small sizes."""
    from PIL import ImageEnhance, ImageOps
    im = ImageOps.autocontrast(im, cutoff=1)
    im = ImageEnhance.Contrast(im).enhance(1.25)
    im = ImageEnhance.Color(im).enhance(1.35)
    return ImageEnhance.Sharpness(im).enhance(1.4)


def _outlined(d: ImageDraw.ImageDraw, xy, text: str, f, fill, stroke: int):
    d.text(xy, text, font=f, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))


def thumbnail_photo(out: Path, text: str, badge: str, photo: Path | None, accent=(255, 205, 40)) -> Path:
    """Click-worthy thumbnail: the real subject photo (licence-checked footage from the video) on the right,
    graded and sharp; huge outlined headline on the left with the key word highlighted; channel badge.
    Without a photo it falls back to a bold gradient. Never built from a video frame."""
    W, H = 1280, 720
    if photo and Path(photo).exists():
        src = Image.open(photo)
        bg = _cover(src, W, H).filter(ImageFilter.GaussianBlur(28))
        bg = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.55)
        subj = _grade(_cover(src, 700, H))
        img = bg.copy()
        mask = Image.linear_gradient("L").rotate(90).resize((700, H))  # transparent on the left edge, solid on the right
        mask = mask.point(lambda v: min(255, int(v * 2.2)))
        img.paste(subj, (W - 700, 0), mask)
    else:
        img = Image.new("RGB", (W, H), (10, 16, 30))
        glow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(glow).ellipse([W * 0.45, -H * 0.3, W * 1.3, H * 1.2], fill=255)
        glow = glow.filter(ImageFilter.GaussianBlur(160))
        img = Image.composite(Image.new("RGB", (W, H), tuple(int(c * 0.55) for c in accent)), img, glow)
        gd = ImageDraw.Draw(img)
        for gx in range(0, W, 40):
            gd.line([(gx, 0), (gx, H)], fill=(255, 255, 255) if gx % 200 == 0 else (60, 70, 90), width=1)
        for gy in range(0, H, 40):
            gd.line([(0, gy), (W, gy)], fill=(255, 255, 255) if gy % 200 == 0 else (60, 70, 90), width=1)
        img = Image.blend(img, Image.new("RGB", (W, H), (10, 16, 30)), 0.55)
    # dark gradient behind the text for contrast
    shade = Image.linear_gradient("L").rotate(90).resize((W, H)).point(lambda v: int((255 - v) * 0.85))
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, shade)
    d = ImageDraw.Draw(img)

    words = text.upper().split()
    hi = next((i for i, w in enumerate(words) if any(ch.isdigit() for ch in w)), len(words) - 1)
    # largest size where <= 3 lines fit in the left 60%
    best = None
    for size in range(168, 70, -6):
        f = _heavy(size)
        lines, cur = [], []
        for i, w in enumerate(words):
            trial = " ".join(x for _, x in cur + [(i, w)])
            if cur and d.textlength(trial, font=f) > 720:
                lines.append(cur)
                cur = [(i, w)]
            else:
                cur.append((i, w))
        if cur:
            lines.append(cur)
        if len(lines) <= 3 and all(d.textlength(" ".join(x for _, x in ln), font=f) <= 720 for ln in lines):
            best = (size, f, lines)
            break
    size, f, lines = best or (72, _heavy(72), [[(i, w) for i, w in enumerate(words)]])
    lh = int(size * 1.04)
    y = (H - lh * len(lines)) // 2 + 20
    stroke = max(6, size // 14)
    for ln in lines:
        x = 64
        for i, w in ln:
            col = accent if i == hi else (255, 255, 255)
            # drop shadow, then outlined word
            d.text((x + 8, y + 10), w, font=f, fill=(0, 0, 0))
            _outlined(d, (x, y), w, f, col, stroke)
            x += int(d.textlength(w + " ", font=f))
        y += lh
    if badge:
        bf = _heavy(34)
        tw = int(d.textlength(badge.upper(), font=bf))
        d.rounded_rectangle([64, 52, 64 + tw + 40, 52 + 58], radius=10, fill=accent)
        d.text((84, 60), badge.upper(), font=bf, fill=(10, 10, 10))
    return _save(img, out, quality=92)


def thumbnail_doc(out: Path, text: str, channel: str, photo: Path | None, kicker: str = "FULL DOCUMENTARY",
                  accent=(255, 205, 40), style: str = "label") -> Path:
    """Documentary-channel thumbnail (the look of big science/engineering channels): the real photo fills the
    frame, graded for punch; a dark fade at the bottom carries a small coloured kicker and a huge headline;
    the channel name sits bottom-left behind a divider. style="band" puts the kicker in an angled colour band.
    Portrait photos are shown sharp over a blurred, full-frame copy of themselves."""
    W, H = 1280, 720
    if photo and Path(photo).exists():
        src = Image.open(photo).convert("RGB")
        # fill the frame; tall photos (towers, people) are cropped around the upper third, where the subject usually is
        img = _grade(_cover(src, W, H, 0.5, 0.45 if src.width / src.height >= 1.15 else 0.3))
    else:
        img = Image.new("RGB", (W, H), (10, 16, 30))
        glow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(glow).ellipse([W * 0.3, -H * 0.4, W * 1.3, H * 1.0], fill=255)
        img = Image.composite(Image.new("RGB", (W, H), tuple(int(c * 0.5) for c in accent)), img, glow.filter(ImageFilter.GaussianBlur(170)))
    # bottom fade for the text
    fade = Image.linear_gradient("L").resize((W, H)).point(lambda v: 0 if v < 110 else min(235, int((v - 110) * 1.9)))
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, fade)
    d = ImageDraw.Draw(img)

    words = text.upper().split()
    hi = next((i for i, w in enumerate(words) if any(ch.isdigit() for ch in w)), None)
    left = 64
    if channel:  # channel name (stacked, logo-like) + divider, bottom-left
        parts = channel.upper().split()
        rows = [" ".join(parts[: (len(parts) + 1) // 2]), " ".join(parts[(len(parts) + 1) // 2:])] if len(parts) > 1 else [channel.upper()]
        cf = font(SERIF_B, 40)
        while max(d.textlength(r, font=cf) for r in rows) > 250 and cf.size > 22:
            cf = font(SERIF_B, cf.size - 2)
        cw = int(max(d.textlength(r, font=cf) for r in rows))
        cy = H - 60 - int(cf.size * 1.15) * len(rows)
        for r in rows:
            d.text((left, cy), r, font=cf, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
            cy += int(cf.size * 1.15)
        d.line([(left + cw + 28, H - 190), (left + cw + 28, H - 48)], fill=(255, 255, 255), width=4)
        left = left + cw + 58
    avail = W - left - 56
    best = None
    for size in range(110, 48, -4):
        f = _heavy(size)
        lines, cur = [], []
        for i, w in enumerate(words):
            trial = " ".join(x for _, x in cur + [(i, w)])
            if cur and d.textlength(trial, font=f) > avail:
                lines.append(cur)
                cur = [(i, w)]
            else:
                cur.append((i, w))
        if cur:
            lines.append(cur)
        if len(lines) <= 2 and all(d.textlength(" ".join(x for _, x in ln), font=f) <= avail for ln in lines):
            best = (size, f, lines)
            break
    size, f, lines = best or (48, _heavy(48), [[(i, w) for i, w in enumerate(words)]])
    lh = int(size * 1.02)
    y = H - 46 - lh * len(lines)
    kf = _heavy(max(30, size // 3))
    ky = y - int(kf.size * 1.35)
    if kicker:
        if style == "band":
            kw = int(d.textlength(kicker, font=kf))
            band = [(left - 24, ky - 10), (left + kw + 40, ky - 10), (left + kw + 20, ky + kf.size + 14), (left - 44, ky + kf.size + 14)]
            d.polygon(band, fill=accent)
            d.text((left, ky), kicker, font=kf, fill=(10, 10, 10))
        else:
            d.text((left, ky), kicker, font=kf, fill=accent, stroke_width=2, stroke_fill=(0, 0, 0))
    stroke = max(3, size // 22)
    for ln in lines:
        x = left
        for i, w in ln:
            col = accent if i == hi else (255, 255, 255)
            d.text((x + 5, y + 6), w, font=f, fill=(0, 0, 0))
            d.text((x, y), w, font=f, fill=col, stroke_width=stroke, stroke_fill=(0, 0, 0))
            x += int(d.textlength(w + " ", font=f))
        y += lh
    return _save(img, out, quality=92)


def _save(img: Image.Image, out: Path, quality: int = 90) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=quality) if out.suffix.lower() in (".jpg", ".jpeg") else img.save(out)
    return out
