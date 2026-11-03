"""YouTube publishing with least-privilege scopes (youtube.upload + youtube.readonly).

Limits designed around, not hidden:
* The API cannot create channels; the user creates one on YouTube and connects it.
* Unaudited Google Cloud projects can only upload PRIVATE videos; we force private and say so.
* Quota is finite (default 10,000 units/day per project); uploads are capped per user per day and logged.
* Resumable upload: the session URI is stored, so a retry resumes the same upload instead of creating a duplicate.
"""
from __future__ import annotations

import base64
import os
from datetime import date

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import db
from .config import load_json, require, settings
from .storage import store

PLANS = load_json("config/plans.json")
CHUNK = int(os.environ.get("YOUTUBE_CHUNK_BYTES", 8 * 1024 * 1024))
# Overridable only so tests can point at a local fake; production uses Google's endpoints.
OAUTH = os.environ.get("GOOGLE_OAUTH_BASE", "https://oauth2.googleapis.com")
API = os.environ.get("YOUTUBE_API_BASE", "https://www.googleapis.com")


class QuotaExceeded(RuntimeError):
    pass


def _key() -> bytes:
    require("youtube")
    k = base64.b64decode(settings.token_encryption_key)
    if len(k) != 32:
        raise ValueError("TOKEN_ENCRYPTION_KEY must be 32 bytes, base64-encoded")
    return k


def decrypt(blob: str) -> str:
    raw = base64.b64decode(blob)
    iv, tag, ct = raw[:12], raw[12:28], raw[28:]
    return AESGCM(_key()).decrypt(iv, ct + tag, None).decode()


def encrypt(text: str) -> str:
    iv = os.urandom(12)
    out = AESGCM(_key()).encrypt(iv, text.encode(), None)
    ct, tag = out[:-16], out[-16:]
    return base64.b64encode(iv + tag + ct).decode()


def access_token(user_id: str) -> tuple[str, dict]:
    conn = db.q1("select * from youtube_connections where user_id=%s", (user_id,))
    if not conn:
        raise RuntimeError("YouTube is not connected for this user")
    r = requests.post(f"{OAUTH}/token", timeout=30, data={
        "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
        "refresh_token": decrypt(conn["refresh_token_enc"]), "grant_type": "refresh_token"})
    r.raise_for_status()
    tok = r.json()["access_token"]
    db.x("update youtube_connections set access_token_enc=%s, expires_at=now() + interval '50 minutes' where user_id=%s",
         (encrypt(tok), user_id))
    return tok, conn


def charge_quota(user_id: str, op: str) -> None:
    units = PLANS["youtube"]["quota_cost"][op]
    used = db.q1("select coalesce(sum(units),0) u from youtube_quota where day=%s", (date.today(),))["u"]
    if used + units > PLANS["youtube"]["quota_units_per_day_project"]:
        raise QuotaExceeded(f"YouTube API quota for today would be exceeded ({used}+{units}). Try tomorrow or download the MP4.")
    db.x("""insert into youtube_quota (day, user_id, units) values (%s,%s,%s)
            on conflict (day, user_id) do update set units = youtube_quota.units + excluded.units""", (date.today(), user_id, units))


def publish(job: dict) -> dict:
    pub = db.q1("select * from publishes where id=%s", (job["payload"]["publish_id"],))
    if pub["youtube_video_id"]:
        return {"video_id": pub["youtube_video_id"], "skipped": "already uploaded"}  # retry never double-uploads
    project = db.q1("select * from projects where id=%s", (pub["project_id"],))
    render = db.q1("select * from renders where id=%s", (pub["render_id"],))
    uploads_today = db.q1("""select count(*) n from publishes where user_id=%s and youtube_video_id is not null
                             and created_at::date = current_date""", (pub["user_id"],))["n"]
    if uploads_today >= PLANS["youtube"]["daily_upload_cap_per_user"]:
        raise QuotaExceeded("Daily upload cap reached for this account; it resets tomorrow.")
    token, conn = access_token(pub["user_id"])
    privacy = pub["privacy"] if conn["audited_project"] else "private"
    local = settings.work_dir / str(project["id"]) / "publish.mp4"
    store().get(render["mp4_path"], local)
    size = local.stat().st_size
    h = {"Authorization": f"Bearer {token}"}
    session = pub["upload_session_uri"]
    if not session:
        charge_quota(pub["user_id"], "videos.insert")
        body = {"snippet": {"title": project["selected_title"], "description": project["description"],
                            "tags": project["tags"], "categoryId": job["payload"].get("category_id", "27")},
                "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                           "containsSyntheticMedia": bool(job["payload"].get("contains_synthetic_media", True))}}
        if pub["publish_at"] and privacy == "private" and conn["audited_project"]:
            body["status"]["publishAt"] = pub["publish_at"].isoformat()
        r = requests.post(f"{API}/upload/youtube/v3/videos", timeout=60,
                          params={"uploadType": "resumable", "part": "snippet,status"},
                          headers={**h, "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"}, json=body)
        r.raise_for_status()
        session = r.headers["Location"]
        db.x("update publishes set upload_session_uri=%s, status='uploading' where id=%s", (session, pub["id"]))
    # find how much the server already has (resume after crash)
    r = requests.put(session, headers={**h, "Content-Range": f"bytes */{size}"}, timeout=60)
    if r.status_code in (200, 201):
        video = r.json()
    else:
        sent = int(r.headers["Range"].split("-")[1]) + 1 if "Range" in r.headers else 0
        video = None
        with open(local, "rb") as f:
            while sent < size:
                f.seek(sent)
                chunk = f.read(CHUNK)
                end = sent + len(chunk) - 1
                r = requests.put(session, data=chunk, timeout=600,
                                 headers={**h, "Content-Length": str(len(chunk)), "Content-Range": f"bytes {sent}-{end}/{size}"})
                if r.status_code in (200, 201):
                    video = r.json()
                    break
                if r.status_code != 308:
                    r.raise_for_status()
                sent = int(r.headers["Range"].split("-")[1]) + 1
                db.x("update publishes set bytes_sent=%s where id=%s", (sent, pub["id"]))
    vid = video["id"]
    db.x("""update publishes set youtube_video_id=%s, youtube_url=%s, status='uploaded', bytes_sent=%s, privacy=%s where id=%s""",
         (vid, f"https://www.youtube.com/watch?v={vid}", size, privacy, pub["id"]))
    if render["thumb_path"]:
        try:
            charge_quota(pub["user_id"], "thumbnails.set")
            thumb = store().get(render["thumb_path"], settings.work_dir / str(project["id"]) / "thumb.jpg")
            requests.post(f"{API}/upload/youtube/v3/thumbnails/set", params={"videoId": vid}, timeout=120,
                          headers={**h, "Content-Type": "image/jpeg"}, data=thumb.read_bytes()).raise_for_status()
        except requests.HTTPError as e:  # custom thumbnails need a verified channel; the video is still uploaded
            db.x("update publishes set error=%s where id=%s", (f"thumbnail not set: {e}", pub["id"]))
    db.x("update projects set status='published' where id=%s", (project["id"],))
    db.x("update ideas set status='published' where id=%s", (project["idea_id"],))
    return {"video_id": vid, "privacy": privacy}


def revoke(user_id: str) -> None:
    """Settings -> Disconnect: revoke at Google and delete stored tokens."""
    conn = db.q1("select * from youtube_connections where user_id=%s", (user_id,))
    if conn and conn["refresh_token_enc"]:
        try:
            requests.post(f"{OAUTH}/revoke", params={"token": decrypt(conn["refresh_token_enc"])}, timeout=30)
        except Exception:
            pass
    db.x("delete from youtube_connections where user_id=%s", (user_id,))
