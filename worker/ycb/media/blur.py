"""Privacy blur pipeline: detect -> track -> render with FFmpeg. FFmpeg has no face detection, so:

1. Detect faces (YuNet, OpenCV) and license plates (YOLOv9-t plate model, ONNX) on every frame.
   Optional: OCR text regions containing digits (house numbers, phone numbers, addresses) via Tesseract.
2. Track across frames (IoU + centre-distance association, constant-velocity prediction). A track keeps
   blurring for up to `hold_seconds` after its last detection, so the blur does not drop when a person
   turns away, is occluded, or the detector misses a frame; tracks also get a pre-roll so blur starts
   before the first detection.
3. Merge manual boxes and graphic cut-aways from the redaction plan.
4. Render: blur is burned into the pixels (pixelate + gaussian, irreversible) and piped into FFmpeg.

Agency redactions already in the footage are left untouched; this pipeline only adds blur.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..config import settings


@dataclass
class Track:
    id: int
    kind: str
    box: np.ndarray            # x, y, w, h (pixels)
    vel: np.ndarray = field(default_factory=lambda: np.zeros(2))
    misses: int = 0
    hits: int = 1
    frames: dict = field(default_factory=dict)  # frame -> [x,y,w,h]


def iou(a: np.ndarray, b: np.ndarray) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw, ih = max(0, min(ax2, bx2) - max(a[0], b[0])), max(0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    u = a[2] * a[3] + b[2] * b[3] - inter
    return inter / u if u > 0 else 0.0


class Detectors:
    def __init__(self, faces: bool = True, plates: bool = True, text_pii: bool = False):
        self.face = None
        self.plate = None
        self.text_pii = text_pii and shutil.which("tesseract") is not None
        if faces:
            if not settings.face_model.exists():
                raise FileNotFoundError(f"face model missing: {settings.face_model}")
            self.face = cv2.FaceDetectorYN.create(str(settings.face_model), "", (320, 320), 0.55, 0.3, 5000)
        if plates:
            import onnxruntime as rt
            if not settings.plate_model.exists():
                raise FileNotFoundError(f"plate model missing: {settings.plate_model}")
            self.plate = rt.InferenceSession(str(settings.plate_model), providers=["CPUExecutionProvider"])

    def faces(self, frame: np.ndarray) -> list[np.ndarray]:
        h, w = frame.shape[:2]
        s = min(1.0, 960 / w)
        small = cv2.resize(frame, (int(w * s), int(h * s))) if s < 1 else frame
        self.face.setInputSize((small.shape[1], small.shape[0]))
        _, f = self.face.detect(small)
        return [] if f is None else [np.array(r[:4], dtype=float) / s for r in f]

    def plates(self, frame: np.ndarray, conf: float = 0.35) -> list[np.ndarray]:
        h, w = frame.shape[:2]
        r = 384 / max(h, w)
        nh, nw = int(h * r), int(w * r)
        top, left = (384 - nh) // 2, (384 - nw) // 2
        pad = np.full((384, 384, 3), 114, np.uint8)
        pad[top:top + nh, left:left + nw] = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), (nw, nh))
        out = self.plate.run(None, {"images": pad.astype(np.float32).transpose(2, 0, 1)[None] / 255.0})[0]
        res = []
        for row in out:
            if row[6] < conf:
                continue
            x1, y1, x2, y2 = (row[1] - left) / r, (row[2] - top) / r, (row[3] - left) / r, (row[4] - top) / r
            res.append(np.array([x1, y1, x2 - x1, y2 - y1]))
        return res

    def text_regions(self, frame: np.ndarray) -> list[np.ndarray]:
        """Regions with digits (house numbers, phone numbers, addresses, badge numbers)."""
        import pytesseract
        d = pytesseract.image_to_data(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), output_type=pytesseract.Output.DICT,
                                      config="--psm 11")
        out = []
        for i, txt in enumerate(d["text"]):
            if txt and sum(c.isdigit() for c in txt) >= 2 and float(d["conf"][i]) > 50:
                out.append(np.array([d["left"][i], d["top"][i], d["width"][i], d["height"][i]], dtype=float))
        return out


class Tracker:
    def __init__(self, fps: float, hold_seconds: float = 1.0):
        self.tracks: list[Track] = []
        self.dead: list[Track] = []
        self.next_id = 1
        self.max_misses = max(3, int(fps * hold_seconds))

    def step(self, frame_idx: int, kind: str, dets: list[np.ndarray]) -> None:
        live = [t for t in self.tracks if t.kind == kind]
        for t in live:  # predict
            t.box[:2] += t.vel
        unmatched = list(range(len(dets)))
        pairs = []
        for ti, t in enumerate(live):
            for di in unmatched:
                d = dets[di]
                score = iou(t.box, d)
                c1 = t.box[:2] + t.box[2:] / 2
                c2 = d[:2] + d[2:] / 2
                dist = np.linalg.norm(c1 - c2) / max(t.box[2], t.box[3], 1)
                if score > 0.05 or dist < 0.8:
                    pairs.append((score - 0.1 * dist, ti, di))
        pairs.sort(reverse=True)
        used_t, used_d = set(), set()
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            t, d = live[ti], dets[di]
            new_c = d[:2] + d[2:] / 2
            old_c = t.box[:2] + t.box[2:] / 2
            t.vel = 0.6 * t.vel + 0.4 * (new_c - old_c)
            t.box = 0.35 * t.box + 0.65 * d
            t.misses, t.hits = 0, t.hits + 1
            used_t.add(ti)
            used_d.add(di)
        for ti, t in enumerate(live):
            if ti not in used_t:
                t.misses += 1
                t.vel *= 0.85
                t.box[2:] *= 1.02  # grow while lost: covers a head turning or moving
        for di in range(len(dets)):
            if di not in used_d:
                self.tracks.append(Track(self.next_id, kind, dets[di].astype(float).copy()))
                self.next_id += 1
        for t in [t for t in self.tracks if t.kind == kind]:
            if t.misses > self.max_misses:
                self.tracks.remove(t)
                self.dead.append(t)
            else:
                t.frames[frame_idx] = t.box.copy().tolist()

    def all(self) -> list[Track]:
        return self.dead + self.tracks


def analyse(src: Path, faces: bool = True, plates: bool = True, text_pii: bool = False, hold_seconds: float = 1.0,
            pre_roll: int = 8, on_progress=None) -> dict:
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    det = Detectors(faces, plates, text_pii)
    tr = Tracker(fps, hold_seconds)
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if det.face:
            tr.step(i, "face", det.faces(frame))
        if det.plate:
            tr.step(i, "plate", det.plates(frame))
        if det.text_pii and i % max(1, int(fps)) == 0:  # OCR once a second; held by the tracker
            tr.step(i, "text_pii", det.text_regions(frame))
        i += 1
        if on_progress and i % 30 == 0 and n:
            on_progress(i / n)
    cap.release()
    tracks = []
    for t in tr.all():
        if not t.frames:
            continue
        first = min(t.frames)
        for k in range(max(0, first - pre_roll), first):  # pre-roll: blur before first detection
            t.frames[k] = t.frames[first]
        frames = {int(k): v for k, v in sorted(t.frames.items())}
        tracks.append({"id": t.id, "kind": t.kind, "hits": t.hits, "start": min(frames) / fps, "end": max(frames) / fps,
                       "frames": frames})
    return {"fps": fps, "frames": i, "width": w, "height": h, "tracks": tracks}


def _blur_region(frame: np.ndarray, box, pad: float, ellipse: bool) -> None:
    H, W = frame.shape[:2]
    x, y, w, h = box
    x, y, w, h = x - w * pad, y - h * pad, w * (1 + 2 * pad), h * (1 + 2 * pad)
    x0, y0, x1, y1 = int(max(0, x)), int(max(0, y)), int(min(W, x + w)), int(min(H, y + h))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    roi = frame[y0:y1, x0:x1]
    rw, rh = roi.shape[1], roi.shape[0]
    px = cv2.resize(roi, (max(1, rw // 14), max(1, rh // 14)), interpolation=cv2.INTER_AREA)
    px = cv2.resize(px, (rw, rh), interpolation=cv2.INTER_NEAREST)
    k = max(3, (max(rw, rh) // 3) | 1)
    blurred = cv2.GaussianBlur(px, (k, k), 0)
    if ellipse:
        m = np.zeros((rh, rw), np.uint8)
        cv2.ellipse(m, (rw // 2, rh // 2), (rw // 2, rh // 2), 0, 0, 360, 255, -1)
        m = cv2.GaussianBlur(m, (0, 0), max(1, min(rw, rh) / 20))[..., None] / 255.0
        frame[y0:y1, x0:x1] = (blurred * m + roi * (1 - m)).astype(np.uint8)
    else:
        frame[y0:y1, x0:x1] = blurred


def render(src: Path, out: Path, analysis: dict, manual: list[dict] | None = None, cutaways: list[dict] | None = None,
           on_progress=None) -> Path:
    """Burn blur into the pixels. manual: [{t_start,t_end,box:{x,y,w,h} normalised}], cutaways: [{t_start,t_end}]."""
    fps, W, H = analysis["fps"], analysis["width"], analysis["height"]
    by_frame: dict[int, list[tuple[str, list]]] = {}
    for t in analysis["tracks"]:
        for f, b in t["frames"].items():
            by_frame.setdefault(int(f), []).append((t["kind"], b))
    manual = manual or []
    cutaways = cutaways or []
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    ff = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                           "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-i", str(src), "-map", "0:v", "-map", "1:a?",
                           "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-b:a", "160k", "-shortest", str(tmp)], stdin=subprocess.PIPE)
    cap = cv2.VideoCapture(str(src))
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = i / fps
        if any(c["t_start"] <= t <= c["t_end"] for c in cutaways):
            frame = (cv2.GaussianBlur(frame, (0, 0), 45) * 0.35).astype(np.uint8)  # graphic: heavy blur + darken
        for kind, b in by_frame.get(i, []):
            _blur_region(frame, b, pad=0.3 if kind == "face" else 0.2, ellipse=kind == "face")
        for m in manual:
            if m["t_start"] <= t <= m["t_end"]:
                bx = m["box"]
                _blur_region(frame, (bx["x"] * W, bx["y"] * H, bx["w"] * W, bx["h"] * H), pad=0.0, ellipse=False)
        ff.stdin.write(frame.tobytes())
        i += 1
        if on_progress and i % 30 == 0:
            on_progress(i / max(1, analysis["frames"]))
    cap.release()
    ff.stdin.close()
    if ff.wait() != 0:
        raise RuntimeError("ffmpeg failed while writing blurred video")
    tmp.replace(out)
    return out


def save_analysis(analysis: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analysis))
    return path
