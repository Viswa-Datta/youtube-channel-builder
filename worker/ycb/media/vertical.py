"""Vertical (9:16) rendering for YouTube Shorts: photos with fast camera moves, clips cropped to fill the frame, a big
hook title in the first seconds, "AI illustration" marks, and an end prompt pointing to the full documentary."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ff import FPS, run

VW, VH = 1080, 1920
SH = int(VH * 1.15)  # stills are prepared 15% taller than the frame so they can move
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS)]


@dataclass
class VShot:
    kind: str              # photo | clip
    path: Path
    duration: float
    in_sec: float = 0.0
    move: int = 0          # picks the camera move
    label: str = ""        # small top label, e.g. "AI illustration"
    punch: bool = False    # tight punch-in on the same picture (a second angle for fast cutting)


def prepare_vstill(src: Path, out: Path) -> Path:
    """Scale a photo to SH pixels tall (wide photos stay wide, so the camera can pan across them); very tall ones are
    cropped to fill. A light grade makes archive photos pop."""
    from PIL import Image, ImageEnhance, ImageOps
    im = Image.open(src).convert("RGB")
    im = ImageOps.autocontrast(im, cutoff=0.5)
    im = ImageEnhance.Color(ImageEnhance.Contrast(im).enhance(1.08)).enhance(1.12)
    min_w = round(SH * VW / VH)
    w = round(im.width * SH / im.height)
    if w >= min_w:
        im = im.resize((min(w, int(SH * 1.9)), SH), Image.LANCZOS) if w <= SH * 1.9 else \
            im.resize((w, SH), Image.LANCZOS).crop(((w - int(SH * 1.9)) // 2, 0, (w - int(SH * 1.9)) // 2 + int(SH * 1.9), SH))
    else:
        h = round(im.height * min_w / im.width)
        im = im.resize((min_w, h), Image.LANCZOS)
        y = int((h - SH) * 0.4)
        im = im.crop((0, y, min_w, y + SH))
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, quality=92)
    return out


def _still_filter(w: int, d: float, move: int, punch: bool = False) -> str:
    """Wide stills pan across (and push in a little); narrow ones push in or pull out. Always visibly moving.
    punch: a close crop (about 1.4x) that keeps creeping in - reads as a new camera angle on the same scene."""
    p = f"(t/{d:.3f})"
    e = f"(0.5-0.5*cos(PI*{p}))"
    if punch:
        z = f"(1.36+0.10*{e})"
        x = ["(iw-ow)/2", "(iw-ow)*0.3", "(iw-ow)*0.7"][move % 3]
        return (f"crop='ih*{VW}/{VH}':ih,scale=w='trunc({VW}*{z}/2)*2':h='trunc({VH}*{z}/2)*2':eval=frame:flags=bicubic,"
                f"crop={VW}:{VH}:'{x}':'(ih-oh)*0.38'")
    if w >= VW * 1.45:
        z = f"(1+0.10*{e})"
        x = f"(iw-ow)*{e}" if move % 2 == 0 else f"(iw-ow)*(1-{e})"
        return (f"scale=w='trunc({w}*{z}/2)*2':h='trunc({SH}*{z}/2)*2':eval=frame:flags=bicubic,"
                f"crop={VW}:{VH}:'{x}':'(ih-oh)/2'")
    z = f"(1+0.28*{e})" if move % 2 == 0 else f"(1.28-0.28*{e})"
    return (f"crop='ih*{VW}/{VH}':ih,scale=w='trunc({VW}*{z}/2)*2':h='trunc({VH}*{z}/2)*2':eval=frame:flags=bicubic,"
            f"crop={VW}:{VH}:'(iw-{VW})/2':'(ih-{VH})/2'")


def label_png(text: str, out: Path) -> Path:
    from PIL import Image, ImageDraw
    from .cards import SANS_B, font
    img = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(SANS_B, 34)
    t = text.strip().upper()
    tw = d.textlength(t, font=f)
    x, y = 60, 300
    d.rounded_rectangle([x - 4, y - 14, x + tw + 34, y + 50], radius=10, fill=(0, 0, 0, 150))
    d.rectangle([x + 8, y - 2, x + 14, y + 38], fill=(255, 205, 40, 255))
    d.text((x + 26, y - 4), t, font=f, fill=(255, 255, 255, 255))
    img.save(out)
    return out


def _wrap(d, text: str, f, width: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if d.textlength(t, font=f) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def hook_png(text: str, out: Path, accent=(255, 205, 40)) -> Path:
    """Big two-tone hook title in the upper third (white with one accent line), heavy outline for any background."""
    from PIL import Image, ImageDraw
    from .cards import _heavy
    img = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    size = 104
    while True:
        f = _heavy(size)
        lines = _wrap(d, text.upper(), f, VW - 140)
        if len(lines) <= 3 or size <= 64:
            break
        size -= 8
    lh = int(size * 1.08)
    y = 360
    shade = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    ImageDraw.Draw(shade).rectangle([0, y - 60, VW, y + lh * len(lines) + 60], fill=(0, 0, 0, 90))
    img = Image.alpha_composite(img, shade)
    d = ImageDraw.Draw(img)
    for k, ln in enumerate(lines):
        tw = d.textlength(ln, font=f)
        col = accent + (255,) if k == len(lines) - 1 and len(lines) > 1 else (255, 255, 255, 255)
        d.text(((VW - tw) / 2, y + k * lh), ln, font=f, fill=col, stroke_width=8, stroke_fill=(0, 0, 0, 255))
    img.save(out)
    return out


def end_png(out: Path, text: str = "FULL DOCUMENTARY ON THE CHANNEL", accent=(255, 205, 40)) -> Path:
    """Closing prompt: a pill with a play triangle, lower part of the frame (above the Shorts UI)."""
    from PIL import Image, ImageDraw
    from .cards import SANS_B, font
    img = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(SANS_B, 46)
    lines = _wrap(d, text, f, VW - 300)
    lh = 58
    h = lh * len(lines) + 70
    y0 = 1180
    d.rounded_rectangle([90, y0, VW - 90, y0 + h], radius=36, fill=(10, 10, 12, 215), outline=accent + (255,), width=4)
    cx, cy = 175, y0 + h // 2
    d.ellipse([cx - 44, cy - 44, cx + 44, cy + 44], fill=accent + (255,))
    d.polygon([(cx - 14, cy - 22), (cx - 14, cy + 22), (cx + 24, cy)], fill=(10, 10, 12, 255))
    for k, ln in enumerate(lines):
        d.text((250, y0 + 35 + k * lh), ln, font=f, fill=(255, 255, 255, 255))
    img.save(out)
    return out


def shot_video(shot: VShot, out: Path, work: Path) -> Path:
    d = f"{shot.duration:.3f}"
    tmp = out.with_suffix(".part.mp4")
    lab = []
    if shot.label:
        lp = label_png(shot.label, work / f"lab-{abs(hash(shot.label)) % 10**8}.png")
        lab = ["-loop", "1", "-framerate", str(FPS), "-t", d, "-i", str(lp)]
    if shot.kind == "photo":
        from PIL import Image
        w = Image.open(shot.path).width
        vf = f"[0:v]{_still_filter(w, shot.duration, shot.move, shot.punch)},setsar=1,format=yuv420p[v]"
        inp = ["-loop", "1", "-framerate", str(FPS), "-t", d, "-i", str(shot.path)]
    else:
        # clips: fill the vertical frame, off-centre crops alternate so a reused clip looks different
        x = ["(iw-ow)/2", "(iw-ow)*0.3", "(iw-ow)*0.7"][shot.move % 3]
        vf = f"[0:v]scale=-2:{VH}:flags=bicubic,crop={VW}:{VH}:'{x}':0,setsar=1,fps={FPS},format=yuv420p[v]"
        inp = ["-stream_loop", "-1", "-ss", f"{shot.in_sec:.3f}", "-i", str(shot.path)]
    if lab:
        vf += ";[v][1:v]overlay=0:0:shortest=1:enable='lt(t,4)',format=yuv420p[o]"
        mp = "[o]"
    else:
        mp = "[v]"
    run([*inp, *lab, "-filter_complex", vf, "-map", mp, *ENC, "-an", "-t", d, str(tmp)])
    tmp.replace(out)
    return out


def finish(video: Path, audio: Path, hook: Path | None, end: Path | None, total: float, out: Path,
           cover: Path | None = None) -> Path:
    """Overlay the hook (first 2.8 s, fades out) and the end prompt (last 3 s), add the audio. cover: the designed
    thumbnail shown for the very first frames, so it can be picked as the Short's thumbnail in YouTube."""
    ins = ["-i", str(video), "-i", str(audio)]
    fc, last, n = "[0:v]null[v0]", "[v0]", 2
    if hook:
        ins += ["-loop", "1", "-framerate", str(FPS), "-t", f"{total:.3f}", "-i", str(hook)]
        fc += f";[{n}:v]format=rgba,fade=t=out:st=2.4:d=0.5:alpha=1[h];{last}[h]overlay=0:0:enable='lt(t,2.95)'[v1]"
        last, n = "[v1]", n + 1
    if end:
        st = max(0.0, total - 3.0)
        ins += ["-loop", "1", "-framerate", str(FPS), "-t", f"{total:.3f}", "-i", str(end)]
        fc += f";[{n}:v]format=rgba,fade=t=in:st={st:.2f}:d=0.35:alpha=1[e];{last}[e]overlay=0:0:enable='gte(t,{st:.2f})'[v2]"
        last, n = "[v2]", n + 1
    if cover:
        ins += ["-loop", "1", "-framerate", str(FPS), "-t", "0.2", "-i", str(cover)]
        fc += f";[{n}:v]scale={VW}:{VH},format=rgba[c];{last}[c]overlay=0:0:eof_action=pass:enable='lt(t,0.1)'[v3]"
        last = "[v3]"
    tmp = out.with_suffix(".part.mp4")
    run([*ins, "-filter_complex", fc + f";{last}format=yuv420p[vo]", "-map", "[vo]", "-map", "1:a", *ENC,
         "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(tmp)])
    tmp.replace(out)
    return out


def cover_png(picture: Path, text: str, out: Path, accent=(255, 205, 40)) -> Path:
    """The Short's thumbnail (1080x1920): its best picture, darkened at the top, with 2-4 huge words. Text sits in
    the upper-middle so it stays visible when YouTube crops the cover for search and the channel page."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageOps
    from .cards import _heavy
    im = ImageOps.fit(Image.open(picture).convert("RGB"), (VW, VH), Image.LANCZOS, centering=(0.5, 0.45))
    im = ImageEnhance.Color(ImageEnhance.Contrast(im).enhance(1.12)).enhance(1.18).convert("RGBA")
    shade = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for y in range(0, 1150):
        a = int(170 * max(0.0, 1 - abs(y - 560) / 600))
        sd.line([(0, y), (VW, y)], fill=(0, 0, 0, a))
    im = Image.alpha_composite(im, shade)
    d = ImageDraw.Draw(im)
    words = (text or "").upper().strip()
    size = 150
    while True:
        f = _heavy(size)
        lines = _wrap(d, words, f, VW - 120)
        if len(lines) <= 3 or size <= 80:
            break
        size -= 10
    lh = int(size * 1.05)
    y = 520 - lh * len(lines) // 2
    for k, ln in enumerate(lines):
        tw = d.textlength(ln, font=f)
        col = accent + (255,) if k == len(lines) - 1 else (255, 255, 255, 255)
        d.text(((VW - tw) / 2, y + k * lh), ln, font=f, fill=col, stroke_width=12, stroke_fill=(0, 0, 0, 255))
    im.convert("RGB").save(out, quality=92)
    return out
