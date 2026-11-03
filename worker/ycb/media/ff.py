"""FFmpeg/ffprobe helpers and the export acceptance checks."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

W, H, FPS = 1920, 1080, 30


class FFmpegError(RuntimeError):
    pass


def run(args: list[str], timeout: int | None = None) -> str:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", *args], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({p.returncode}): {' '.join(args)[:400]}\n{p.stderr[-3000:]}")
    return p.stderr


def probe(path: Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise FFmpegError(p.stderr)
    return json.loads(p.stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def has_audio_stream(path: Path) -> bool:
    return any(s["codec_type"] == "audio" for s in probe(path)["streams"])


def loudness(path: Path) -> dict:
    """Integrated loudness (LUFS), true peak and LRA via ebur128."""
    err = run(["-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    summary = err[err.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    lra = re.search(r"LRA:\s+(-?[\d.]+) LU", summary)
    tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary)
    return {"integrated_lufs": float(i.group(1)) if i else None, "lra": float(lra.group(1)) if lra else None,
            "true_peak_dbfs": float(tp.group(1)) if tp else None}


def silence_ratio(path: Path, noise_db: int = -45, min_d: float = 2.0) -> float:
    """Fraction of the programme that is silent (guards against an audio stream that exists but is empty)."""
    err = run(["-i", str(path), "-vn", "-af", f"silencedetect=n={noise_db}dB:d={min_d}", "-f", "null", "-"])
    total = 0.0
    for m in re.finditer(r"silence_duration: ([\d.]+)", err):
        total += float(m.group(1))
    d = duration(path)
    return total / d if d else 1.0


def acceptance(path: Path) -> dict:
    """The export test from the spec: audio stream present, not silent, loudness near -14 LUFS."""
    info = probe(path)
    streams = info["streams"]
    a = [s for s in streams if s["codec_type"] == "audio"]
    v = [s for s in streams if s["codec_type"] == "video"]
    res = {"has_audio": bool(a), "has_video": bool(v), "duration": float(info["format"]["duration"]),
           "audio_codec": a[0]["codec_name"] if a else None, "video_codec": v[0]["codec_name"] if v else None,
           "width": v[0].get("width") if v else None, "height": v[0].get("height") if v else None,
           "chapters": len(info.get("chapters", [])), "subtitle_streams": sum(s["codec_type"] == "subtitle" for s in streams)}
    if a:
        res.update(loudness(path))
        res["silence_ratio"] = round(silence_ratio(path), 4)
    res["pass"] = bool(a) and bool(v) and res.get("silence_ratio", 1) < 0.2 and \
        res.get("integrated_lufs") is not None and abs(res["integrated_lufs"] + 14) <= 1.5
    return res
