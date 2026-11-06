"""Scene composition and final assembly (FFmpeg).

Final mix: narration (with audio redactions) + music ducked under voice (sidechain) -> two-pass loudnorm to
-14 LUFS integrated / -1.5 dBTP -> AAC 48 kHz. Captions burned in and/or muxed as a subtitle track.
Chapters written as MP4 chapter markers. The export is rejected if ffprobe finds no audio stream.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .ff import FPS, H, W, duration, run

ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS)]


@dataclass
class Segment:
    kind: str           # image | clip
    path: Path
    duration: float
    in_sec: float = 0.0
    pan: str = "right"  # image move: in | out | left | right | up
    photo: bool = False  # a real photograph (gets framing + grade); generated cards are drawn at the right size
    make: Callable[[Path, float], Path] | None = None  # kind "anim": renders the animation at the exact length


XFADE = 0.6  # crossfade between shots inside a scene (seconds)
STILL_W, STILL_H = 2400, 1350  # stills are prepared 25% larger than the frame so they can zoom and pan


def prepare_still(src: Path, out: Path) -> Path:
    """Frame a photo for 16:9 without distortion: wide photos fill the frame; tall or square ones sit sharp over a
    blurred, darkened copy of themselves (documentary style). A light grade makes archive photos pop."""
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    im = Image.open(src).convert("RGB")
    im = ImageOps.autocontrast(im, cutoff=0.5)
    im = ImageEnhance.Contrast(im).enhance(1.08)
    im = ImageEnhance.Color(im).enhance(1.12)
    W2, H2 = STILL_W, STILL_H
    ar = im.width / im.height
    if ar >= 1.45:
        k = max(W2 / im.width, H2 / im.height)
        r = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
        x, y = (r.width - W2) // 2, int((r.height - H2) * 0.45)
        canvas = r.crop((x, y, x + W2, y + H2))
    else:
        k = max(W2 / im.width, H2 / im.height)
        bg = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
        bg = bg.crop(((bg.width - W2) // 2, (bg.height - H2) // 2, (bg.width - W2) // 2 + W2, (bg.height - H2) // 2 + H2))
        bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(40))).enhance(0.45)
        fh = int(H2 * 0.92)
        fg = im.resize((max(1, round(im.width * fh / im.height)), fh), Image.LANCZOS)
        if fg.width > W2 * 0.92:
            fw = int(W2 * 0.92)
            fg = im.resize((fw, max(1, round(im.height * fw / im.width))), Image.LANCZOS)
        bg.paste(fg, ((W2 - fg.width) // 2, (H2 - fg.height) // 2))
        canvas = bg
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=92)
    return out


def _move_filter(move: str, d: float) -> str:
    """Slow camera move over a STILL_W x STILL_H still, output W x H. Zooms use per-frame scaling (smooth, no jitter)."""
    p = f"(t/{d:.3f})"
    ease = f"(0.5-0.5*cos(PI*{p}))"  # ease in/out
    if move in ("in", "out"):
        z = f"(1+0.18*{ease})" if move == "in" else f"(1.18-0.18*{ease})"
        return (f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame:flags=bicubic,"
                f"crop={W}:{H}:'(iw-{W})/2':'(ih-{H})/2'")
    if move == "up":
        return f"scale={STILL_W}:{STILL_H},crop={W}:{H}:'(iw-ow)/2':'(ih-oh)*(1-{ease})'"
    x = f"(iw-ow)*{ease}" if move == "right" else f"(iw-ow)*(1-{ease})"
    return f"scale={STILL_W}:{STILL_H},crop={W}:{H}:'{x}':'(ih-oh)/2'"


def segment_to_video(seg: Segment, out: Path) -> Path:
    tmp = out.with_suffix(".part.mp4")
    d = f"{seg.duration:.3f}"
    if seg.kind == "anim":
        seg.make(tmp, seg.duration)
        tmp.replace(out)
        return out
    if seg.kind == "image":
        src = seg.path
        if seg.photo:
            src = prepare_still(seg.path, out.with_suffix(".still.jpg"))
        vf = f"{_move_filter(seg.pan, seg.duration)},setsar=1,format=yuv420p"
        run(["-loop", "1", "-framerate", str(FPS), "-t", d, "-i", str(src), "-vf", vf, *ENC, "-an", "-t", d, str(tmp)])
    else:
        # clips: fill the frame (crop the edges) when the aspect is close to 16:9, otherwise blurred side fill
        vf = (f"split[a][b];[b]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:2,eq=brightness=-0.12[bg];"
              f"[a]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,"
              f"setsar=1,fps={FPS},format=yuv420p")
        run(["-stream_loop", "-1", "-ss", f"{seg.in_sec:.3f}", "-i", str(seg.path), "-t", d, "-filter_complex", vf, *ENC, "-an", str(tmp)])
    tmp.replace(out)
    return out


def concat_videos(parts: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    tmp = out.with_suffix(".part.mp4")
    run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(tmp)])
    tmp.replace(out)
    return out


def xfade_videos(parts: list[Path], durations: list[float], out: Path, t: float = XFADE, fades: bool = True) -> Path:
    """Join shots with crossfades. Long scenes are joined in groups of 8 and the groups crossfaded again, so FFmpeg
    never holds dozens of 1080p inputs open at once. With fades=True the scene opens from and closes to black."""
    if len(parts) > 8:
        groups, gd = [], []
        for g in range(0, len(parts), 8):
            gp, gdu = parts[g:g + 8], durations[g:g + 8]
            o = out.with_name(f"{out.stem}-g{g // 8}.mp4")
            xfade_videos(gp, gdu, o, t, fades=False)
            groups.append(o)
            gd.append(sum(gdu) - t * (len(gp) - 1))
        res = xfade_videos(groups, gd, out, t, fades=fades)
        for o in groups:
            o.unlink(missing_ok=True)
        return res
    total = sum(durations) - t * (len(parts) - 1)
    fade = f"fade=t=in:d=0.4,fade=t=out:st={max(0, total - 0.4):.3f}:d=0.4," if fades else ""
    if len(parts) == 1:
        run(["-i", str(parts[0]), "-vf", f"{fade}format=yuv420p", *ENC, "-an", str(out)])
        return out
    args: list[str] = []
    for p in parts:
        args += ["-i", str(p)]
    fc, last, offset = [], "0:v", 0.0
    for i in range(1, len(parts)):
        offset += durations[i - 1] - t
        lbl = f"x{i}"
        fc.append(f"[{last}]settb=AVTB,fps={FPS}[p{i}];[{i}:v]settb=AVTB,fps={FPS}[q{i}];"
                  f"[p{i}][q{i}]xfade=transition=fade:duration={t:.3f}:offset={offset:.3f}[{lbl}]")
        last = lbl
    fc.append(f"[{last}]{fade}format=yuv420p[v]")
    run([*args, "-filter_complex", ";".join(fc), "-map", "[v]", *ENC, "-an", str(out)])
    return out


def scene_video(segments: list[Segment], work: Path, out: Path) -> Path:
    """Render each shot (each one XFADE longer, except the last, so crossfades keep the scene length exact)."""
    parts, durs = [], []
    for i, s in enumerate(segments):
        extra = XFADE if i < len(segments) - 1 else 0.0
        seg = Segment(s.kind, s.path, s.duration + extra, s.in_sec, s.pan, s.photo, s.make)
        p = work / f"seg-{i:03d}.mp4"
        segment_to_video(seg, p)
        parts.append(p)
        durs.append(seg.duration)
    return xfade_videos(parts, durs, out)


def concat_audio(wavs: list[Path], out: Path, mutes: list[tuple[float, float]] | None = None) -> Path:
    """Concatenate scene narration at 48 kHz and apply audio redactions (mute ranges)."""
    inputs = []
    for w in wavs:
        inputs += ["-i", str(w)]
    n = len(wavs)
    fc = "".join(f"[{i}:a]aresample=48000,aformat=channel_layouts=mono[a{i}];" for i in range(n))
    fc += "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[cat]"
    last = "cat"
    if mutes:
        expr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in mutes)
        fc += f";[cat]volume=enable='{expr}':volume=0[m]"
        last = "m"
    run([*inputs, "-filter_complex", fc, "-map", f"[{last}]", "-c:a", "pcm_s16le", str(out)])
    return out


def ambient_bed(seconds: float, out: Path) -> Path:
    """A quiet generated drone/pad (made by the app, so no licence needed) when no library track is chosen."""
    d = f"{seconds + 2:.2f}"
    fc = ("[0]volume=0.05[a];[1]volume=0.035[b];[2]volume=0.025[c];[3]lowpass=f=320,volume=0.5[n];"
          "[a][b][c][n]amix=inputs=4:normalize=0,tremolo=f=0.1:d=0.35,aecho=0.6:0.5:900:0.35,"
          "lowpass=f=1800,loudnorm=I=-16:TP=-3,afade=t=in:d=4" + f",afade=t=out:st={seconds - 4:.2f}:d=4[o]")
    run(["-f", "lavfi", "-i", f"sine=f=110:d={d}", "-f", "lavfi", "-i", f"sine=f=164.81:d={d}",
         "-f", "lavfi", "-i", f"sine=f=220:d={d}", "-f", "lavfi", "-i", f"anoisesrc=color=brown:a=0.02:d={d}",
         "-filter_complex", fc, "-map", "[o]", "-ac", "2", "-ar", "48000", "-t", f"{seconds:.3f}", str(out)])
    return out


def mix(narration: Path, music: Path | None, out: Path, music_db: float = -20.0) -> tuple[Path, dict]:
    """Narration + ducked music, then two-pass loudnorm to -14 LUFS. Returns (aac m4a, loudnorm stats)."""
    dur = duration(narration)
    pre = out.with_suffix(".premix.wav")
    if music is not None:
        fc = (f"[0:a]aformat=channel_layouts=stereo,asplit=2[v][sc];"
              f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{dur:.3f},aformat=channel_layouts=stereo,volume={music_db}dB[m];"
              f"[m][sc]sidechaincompress=threshold=0.02:ratio=10:attack=20:release=600:makeup=1[duck];"
              f"[v][duck]amix=inputs=2:normalize=0:duration=first[o]")
        run(["-i", str(narration), "-i", str(music), "-filter_complex", fc, "-map", "[o]", "-ar", "48000",
             "-c:a", "pcm_s16le", str(pre)])
    else:
        run(["-i", str(narration), "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(pre)])
    target = "I=-14:TP=-1.5:LRA=11"
    err = run(["-i", str(pre), "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"])
    stats = json.loads(err[err.rfind("{"):err.rfind("}") + 1])
    af = (f"loudnorm={target}:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
          f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:offset={stats['target_offset']}:linear=true")
    run(["-i", str(pre), "-af", af, "-ar", "48000", "-c:a", "aac", "-b:a", "192k", str(out)])
    pre.unlink(missing_ok=True)
    return out, stats


def ffmetadata(chapters: list[tuple[float, float, str]], title: str) -> str:
    def esc(s: str) -> str:
        return re.sub(r"([=;#\\\n])", r"\\\1", s)
    out = [";FFMETADATA1", f"title={esc(title)}"]
    for a, b, t in chapters:
        out += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(a * 1000)}", f"END={int(b * 1000)}", f"title={esc(t)}"]
    return "\n".join(out) + "\n"


def final_mux(video: Path, audio: Path, srt: Path | None, meta: Path, out: Path, burn: bool, sidecar: bool,
              manual_boxes: list[dict] | None = None) -> Path:
    """Mux programme video + mixed audio + chapters; burn captions and reviewer blur boxes into the pixels."""
    tmp = out.with_suffix(".part.mp4")
    args = ["-i", str(video), "-i", str(audio), "-i", str(meta)]
    if sidecar and srt:
        args += ["-i", str(srt)]
    steps: list[str] = []
    for b in manual_boxes or []:  # crop -> heavy blur -> overlay back, only during [t_start, t_end]
        x, y = int(b["x"] * W), int(b["y"] * H)
        w, h = max(8, int(b["w"] * W)) // 2 * 2, max(8, int(b["h"] * H)) // 2 * 2
        r = max(2, min(w, h) // 4)
        steps.append(f"split[m][c];[c]crop={w}:{h}:{x}:{y},boxblur={r}:3[bl];[m][bl]overlay={x}:{y}:enable='between(t,{b['t_start']:.3f},{b['t_end']:.3f})'")
    if burn and srt:
        style = ("FontName=Inter,FontSize=17,PrimaryColour=&H00F0F0F0,OutlineColour=&H80000000,BackColour=&H99000000,"
                 "BorderStyle=4,Outline=0,Shadow=0,MarginV=42,Alignment=2")
        srt_esc = str(srt).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        steps.append(f"subtitles='{srt_esc}':force_style='{style}'")
    if steps:
        graph = "[0:v]" + "".join(f"{st}[s{i}];[s{i}]" for i, st in enumerate(steps[:-1])) + steps[-1] + "[vout]"
        args += ["-filter_complex", graph, *ENC, "-map", "[vout]"]
    else:
        args += ["-c:v", "copy", "-map", "0:v:0"]
    args += ["-map", "1:a:0"]
    if sidecar and srt:
        args += ["-map", "3:s:0", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng"]
    args += ["-map_metadata", "2", "-map_chapters", "2", "-c:a", "copy", "-movflags", "+faststart", "-shortest", str(tmp)]
    run(args)
    tmp.replace(out)
    return out
