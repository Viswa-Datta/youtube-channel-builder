"""Upload test against a local fake of Google's OAuth + YouTube resumable-upload protocol.

Checks: checklist gate, private-only when the API project is unaudited, title/description/tags/chapters sent,
thumbnail set, resumable upload survives a failure mid-upload and resumes from the server's byte offset
(no second session, no duplicate video), and a second publish of the same render is refused.
"""
import base64
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from werkzeug.serving import make_server

STATE = {"sessions": 0, "videos": [], "received": bytearray(), "fail_after_first_chunk": True, "meta": None, "thumb": 0}


def fake_google():
    from flask import Flask, Response, jsonify, request
    app = Flask("fakeyt")

    @app.post("/token")
    def token():
        assert request.form["grant_type"] == "refresh_token"
        return jsonify(access_token="fake-access", expires_in=3600)

    @app.post("/upload/youtube/v3/videos")
    def init():
        assert request.args["uploadType"] == "resumable"
        STATE["sessions"] += 1
        STATE["meta"] = request.get_json()
        STATE["size"] = int(request.headers["X-Upload-Content-Length"])
        r = Response(status=200)
        r.headers["Location"] = request.host_url + "session/1"
        return r

    @app.put("/session/1")
    def put():
        cr = request.headers["Content-Range"]
        have = len(STATE["received"])
        if cr.startswith("bytes */"):
            if have >= STATE["size"]:
                return jsonify(id=STATE["videos"][-1])
            r = Response(status=308)
            if have:
                r.headers["Range"] = f"bytes=0-{have - 1}"
            return r
        start = int(cr.split()[1].split("-")[0])
        assert start == have, f"client resumed at {start}, server has {have}"
        STATE["received"] += request.get_data()
        if STATE["fail_after_first_chunk"]:
            STATE["fail_after_first_chunk"] = False
            return Response("backend error", status=503)  # connection dies after the server stored the chunk
        if len(STATE["received"]) >= STATE["size"]:
            STATE["videos"].append(f"vid{len(STATE['videos']) + 1}")
            return jsonify(id=STATE["videos"][-1])
        r = Response(status=308)
        r.headers["Range"] = f"bytes=0-{len(STATE['received']) - 1}"
        return r

    @app.post("/upload/youtube/v3/thumbnails/set")
    def thumb():
        STATE["thumb"] += 1
        return jsonify(ok=True)

    return app


@pytest.fixture(scope="module")
def server():
    srv = make_server("127.0.0.1", 0, fake_google())
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_upload_private_resumable_no_duplicates(db, project, server, monkeypatch):
    from ycb import main, queue, youtube
    from ycb.config import settings
    monkeypatch.setattr(youtube, "OAUTH", server)
    monkeypatch.setattr(youtube, "API", server)
    monkeypatch.setattr(youtube, "CHUNK", 256 * 1024)
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(settings, "token_encryption_key", key)
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "secret")

    pid, uid = str(project["project"]), str(project["user"])
    root = Path(os.environ["LOCAL_STORAGE_DIR"])
    mp4 = root / f"projects/{pid}/renders/v-1.mp4"
    mp4.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=8",
                    "-f", "lavfi", "-i", "sine=f=220:duration=8", "-c:v", "libx264", "-preset", "veryfast", "-crf", "10", "-c:a", "aac", "-shortest", str(mp4)], check=True)
    thumb = root / f"projects/{pid}/renders/thumb-v-1.jpg"
    thumb.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    rid = db.execute("""insert into renders (project_id, version, status, has_audio, mp4_path, thumb_path) values (%s,1,'succeeded',true,%s,%s) returning id""",
                     (pid, f"projects/{pid}/renders/v-1.mp4", f"projects/{pid}/renders/thumb-v-1.jpg")).fetchone()["id"]
    desc = "A calm explainer.\n\n0:00 Cold open\n4:17 The timeline\n8:34 Bodycam\n\nNarration is AI-generated."
    db.execute("""update projects set selected_title='How a Cold Case Is Solved', description=%s, tags=array['cold case','documentary'],
                  disclosure_line='Narration is AI-generated.' where id=%s""", (desc, pid))
    db.execute("""insert into assets (project_id, scene_id, kind, tier, license, selected) values (%s,%s,'card','generated','Licensed',true)""", (pid, project["scene"]))
    db.execute("""insert into assets (project_id, kind, tier, license, selected, storage_path) values (%s,'thumbnail','generated','Licensed',true,%s)""",
               (pid, f"projects/{pid}/renders/thumb-v-1.jpg"))
    db.execute("insert into blur_signoffs (project_id, render_id, user_id, frames_checked, scrubbed_full_video) values (%s,%s,%s,240,true)", (pid, rid, uid))
    db.execute("update projects set fact_check_signed_at=now(), approved_at=now() where id=%s", (pid,))
    db.execute("""insert into youtube_connections (user_id, channel_id, channel_title, scopes, access_token_enc, refresh_token_enc, audited_project)
                  values (%s,'UC123','Test Channel', array['https://www.googleapis.com/auth/youtube.upload','https://www.googleapis.com/auth/youtube.readonly'], %s, %s, false)""",
               (uid, youtube.encrypt("old"), youtube.encrypt("refresh-token")))
    pub = db.execute("insert into publishes (project_id, render_id, user_id, privacy) values (%s,%s,%s,'public') returning id", (pid, rid, uid)).fetchone()["id"]
    job = queue.enqueue(user_id=uid, kind="publish", project_id=pid, render_id=str(rid), payload={"publish_id": str(pub)}, idempotency_key=f"publish:{pub}")

    main.run_one(["publish"])  # attempt 1 dies mid-upload
    j = db.execute("select status, attempts from jobs where id=%s", (job["id"],)).fetchone()
    assert j["status"] == "queued" and j["attempts"] == 1
    db.execute("update jobs set run_after=now() where id=%s", (job["id"],))
    main.run_one(["publish"])  # attempt 2 resumes from the server's offset

    p = db.execute("select * from publishes where id=%s", (pub,)).fetchone()
    assert p["status"] == "uploaded" and p["youtube_video_id"] == "vid1" and p["youtube_url"].endswith("vid1")
    assert p["privacy"] == "private", "unaudited projects must upload private"
    assert STATE["sessions"] == 1 and len(STATE["videos"]) == 1 and bytes(STATE["received"]) == mp4.read_bytes()
    assert STATE["meta"]["status"]["privacyStatus"] == "private"
    assert STATE["meta"]["snippet"]["title"] == "How a Cold Case Is Solved" and "0:00 Cold open" in STATE["meta"]["snippet"]["description"]
    assert STATE["meta"]["snippet"]["tags"] == ["cold case", "documentary"] and STATE["thumb"] == 1
    assert db.execute("select units from youtube_quota where user_id=%s", (uid,)).fetchone()["units"] == 150

    # running the job again (e.g. a duplicate delivery) does not upload again
    db.execute("update jobs set status='queued', run_after=now() where id=%s", (job["id"],))
    main.run_one(["publish"])
    assert STATE["sessions"] == 1 and len(STATE["videos"]) == 1
    # and a second publish row for the same render is refused by the database
    import psycopg
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("insert into publishes (project_id, render_id, user_id) values (%s,%s,%s)", (pid, rid, uid))
