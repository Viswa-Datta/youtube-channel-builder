"""Builds the blur acceptance clip with known ground-truth boxes.

Face: a real photographed face (scikit-image 'astronaut', NASA public-domain photo of Eileen Collins) that
moves across the frame, scales, rotates, mirrors (subject turning), drifts partly out of frame, and is briefly
occluded. Plate: a US-style plate on a car body that drives across the frame.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS, SECONDS = 1280, 720, 30, 8


def face_sprite():
    from skimage import data
    img = cv2.cvtColor(data.astronaut(), cv2.COLOR_RGB2BGR)
    det = cv2.FaceDetectorYN.create("/models/face_detection_yunet_2023mar.onnx" if Path("/models/face_detection_yunet_2023mar.onnx").exists()
                                    else str(Path.home() / "models/face_detection_yunet_2023mar.onnx"), "", (img.shape[1], img.shape[0]))
    _, f = det.detect(img)
    x, y, w, h = [int(v) for v in f[0][:4]]
    # crop head-and-shoulders around the face so it looks like a person in frame
    cx, cy = x + w // 2, y + h // 2
    s = int(max(w, h) * 1.9)
    crop = img[max(0, cy - s // 2): cy + s // 2, max(0, cx - s // 2): cx + s // 2].copy()
    fx, fy = x - max(0, cx - s // 2), y - max(0, cy - s // 2)
    return crop, (fx, fy, w, h)


def plate_sprite():
    img = Image.new("RGB", (420, 240), (40, 60, 110))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 419, 239], 30, fill=(40, 60, 110))
    d.rectangle([130, 120, 290, 200], fill=(245, 245, 240), outline=(20, 20, 20), width=4)
    d.text((210, 165), "7ABC123", font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 44),
           fill=(20, 30, 90), anchor="mm")
    d.text((210, 132), "CALIFORNIA", font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14),
           fill=(160, 20, 20), anchor="mm")
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR), (130, 120, 160, 80)


def background(t: float) -> np.ndarray:
    bg = np.zeros((H, W, 3), np.uint8)
    bg[:] = (62, 66, 70)
    cv2.rectangle(bg, (0, int(H * 0.62)), (W, H), (48, 50, 52), -1)  # road
    for k in range(0, W, 160):  # lane marks moving
        x = int((k - t * 120) % W)
        cv2.rectangle(bg, (x, int(H * 0.8)), (x + 80, int(H * 0.8) + 8), (180, 180, 170), -1)
    return bg


def paste(dst, src, x, y):
    h, w = src.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    dst[y0:y1, x0:x1] = src[y0 - y:y1 - y, x0 - x:x1 - x]


def make(out: Path) -> dict:
    face, (fx, fy, fw, fh) = face_sprite()
    plate, (px, py, pw, ph) = plate_sprite()
    gt = []
    raw = out.with_suffix(".raw.mp4")
    vw = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    n = FPS * SECONDS
    for i in range(n):
        t = i / FPS
        frame = background(t)
        boxes = {}
        # face: scale 0.6->1.0, moves left->right, rotates +-18deg, mirrored 3.0-4.5s (turning), exits right edge at end
        sc = 0.6 + 0.4 * (i / n)
        ang = 18 * np.sin(t * 1.7)
        sprite = cv2.resize(face, None, fx=sc, fy=sc)
        mirrored = 3.0 <= t < 4.5
        if mirrored:
            sprite = cv2.flip(sprite, 1)
        M = cv2.getRotationMatrix2D((sprite.shape[1] / 2, sprite.shape[0] / 2), ang, 1.0)
        sprite = cv2.warpAffine(sprite, M, (sprite.shape[1], sprite.shape[0]), borderValue=(62, 66, 70))
        sx = int(60 + (W - 120) * (i / n) * 1.05) - sprite.shape[1] // 2
        sy = int(H * 0.08 + 30 * np.sin(t * 2))
        paste(frame, sprite, sx, sy)
        ffx = fx * sc
        if mirrored:
            ffx = sprite.shape[1] - ffx - fw * sc
        cx, cy = sx + ffx + fw * sc / 2, sy + fy * sc + fh * sc / 2
        r = np.deg2rad(-ang)
        ox, oy = cx - (sx + sprite.shape[1] / 2), cy - (sy + sprite.shape[0] / 2)
        cx2 = sx + sprite.shape[1] / 2 + ox * np.cos(r) - oy * np.sin(r)
        cy2 = sy + sprite.shape[0] / 2 + ox * np.sin(r) + oy * np.cos(r)
        boxes["face"] = [cx2 - fw * sc / 2, cy2 - fh * sc / 2, fw * sc, fh * sc]
        # occluder passes in front of the face 5.5-5.8s (half covers it)
        if 5.5 <= t < 5.8:
            b = boxes["face"]
            cv2.rectangle(frame, (int(b[0] - 10), int(b[1] + b[3] * 0.5)), (int(b[0] + b[2] + 10), int(b[1] + b[3] + 40)),
                          (30, 30, 30), -1)
        # car with plate: drives right->left along the road
        cxp = int(W - (W + 200) * (i / n))
        cyp = int(H * 0.55)
        paste(frame, plate, cxp, cyp)
        boxes["plate"] = [cxp + px, cyp + py, pw, ph]
        vw.write(frame)
        gt.append(boxes)
    vw.release()
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw), "-f", "lavfi", "-i",
                    f"sine=frequency=220:duration={SECONDS}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)], check=True)
    raw.unlink()
    (out.with_suffix(".gt.json")).write_text(json.dumps(gt))
    return {"frames": n, "gt": gt}


def visible_fraction(b) -> float:
    x, y, w, h = b
    iw = max(0, min(W, x + w) - max(0, x))
    ih = max(0, min(H, y + h) - max(0, y))
    return iw * ih / (w * h) if w * h else 0


def sharpness(img, b) -> float:
    x, y, w, h = [int(v) for v in b]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return 0.0
    roi = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(roi, cv2.CV_64F).var())


def verify(original: Path, blurred: Path, gt: list[dict]) -> dict:
    """Every frame where an object is at least 25% visible must have its region blurred
    (detail energy cut by >= 85% vs the original frame)."""
    a, b = cv2.VideoCapture(str(original)), cv2.VideoCapture(str(blurred))
    res = {"face": {"checked": 0, "blurred": 0, "worst_ratio": 0.0, "failed_frames": []},
           "plate": {"checked": 0, "blurred": 0, "worst_ratio": 0.0, "failed_frames": []}}
    i = 0
    while True:
        ok1, f1 = a.read()
        ok2, f2 = b.read()
        if not (ok1 and ok2):
            break
        for kind in ("face", "plate"):
            box = gt[i][kind]
            if visible_fraction(box) < 0.25:
                continue
            s1, s2 = sharpness(f1, box), sharpness(f2, box)
            if s1 < 20:  # nothing to blur (fully occluded/flat)
                continue
            ratio = s2 / s1
            r = res[kind]
            r["checked"] += 1
            r["worst_ratio"] = max(r["worst_ratio"], round(ratio, 4))
            if ratio <= 0.15:
                r["blurred"] += 1
            else:
                r["failed_frames"].append(i)
        i += 1
    for r in res.values():
        r["pass"] = r["checked"] > 0 and r["blurred"] == r["checked"]
        r["failed_frames"] = r["failed_frames"][:20]
    res["frames"] = i
    res["pass"] = res["face"]["pass"] and res["plate"]["pass"]
    return res
