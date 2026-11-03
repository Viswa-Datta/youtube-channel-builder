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

from .ff import FPS, H, W, duration, run

ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS)]


@dataclass
class Segment:
    kind: str           # image | clip
    path: Path
    duration: float
    in_sec: float = 0.0
    pan: str = "right"  # image pan direction


def segment_to_video(seg: Segment, out: Path) -> Path:
    tmp = out.with_suffix(".part.mp4")
    d = f"{seg.duration:.3f}"
    if seg.kind == "image":
        # slow pan across a 10%-oversized still: cheap, no zoompan
        x = "(iw-ow)*t/{d}".format(d=d) if seg.pan == "right" else "(iw-ow)*(1-t/{d})".format(d=d)
        vf = f"scale=2112:1188,crop={W}:{H}:x='{x}':y='(ih-oh)/2',format=yuv420p,fade=t=in:st=0:d=0.4,fade=t=out:st={max(0, seg.duration - 0.4):.3f}:d=0.4"
        run(["-loop", "1", "-framerate", str(FPS), "-t", d, "-i", str(seg.path), "-vf", vf, *ENC, "-tune", "stillimage",
             "-an", "-t", d, str(tmp)])
    else:
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
              f"setsar=1,fps={FPS},format=yuv420p,fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, seg.duration - 0.3):.3f}:d=0.3")
        run(["-stream_loop", "-1", "-ss", f"{seg.in_sec:.3f}", "-i", str(seg.path), "-t", d, "-vf", vf, *ENC, "-an", str(tmp)])
    tmp.replace(out)
    return out


def concat_videos(parts: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    tmp = out.with_suffix(".part.mp4")
    run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(tmp)])
    tmp.replace(out)
    return out


def scene_video(segments: list[Segment], work: Path, out: Path) -> Path:
    parts = []
    for i, s in enumerate(segments):
        p = work / f"seg-{i:03d}.mp4"
        segment_to_video(s, p)
        parts.append(p)
    return concat_videos(parts, out)


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
