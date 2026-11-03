"""Project media storage. Layout per project (spec):
    projects/<project_id>/audio/scene-<n>.wav|mp3
    projects/<project_id>/clips/scene-<n>.mp4
    projects/<project_id>/renders/v-<n>.mp4, thumb-v-<n>.jpg, captions.srt
Local driver for development/tests; Supabase Storage REST for production (R2 works via the same paths
if you swap the driver for an S3 client).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import requests

from .config import require, settings


def project_key(project_id: str, *parts: str) -> str:
    return "/".join(["projects", project_id, *parts])


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root

    def path(self, key: str) -> Path:
        return self.root / key

    def put(self, local: Path, key: str, content_type: str | None = None) -> str:
        dest = self.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        shutil.copyfile(local, tmp)
        os.replace(tmp, dest)  # atomic: a killed worker never leaves a half-written object
        return key

    def get(self, key: str, local: Path) -> Path:
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.path(key), local)
        return local

    def exists(self, key: str) -> bool:
        return self.path(key).exists()

    def delete_prefix(self, prefix: str) -> None:
        shutil.rmtree(self.path(prefix), ignore_errors=True)


class SupabaseStorage:
    def __init__(self) -> None:
        require("supabase")
        self.base = settings.supabase_url.rstrip("/") + "/storage/v1"
        self.h = {"Authorization": f"Bearer {settings.supabase_service_key}", "apikey": settings.supabase_service_key}
        self.bucket = settings.storage_bucket

    def put(self, local: Path, key: str, content_type: str | None = None) -> str:
        if local.stat().st_size > 6 * 1024 * 1024:
            return self._put_resumable(local, key, content_type)
        with open(local, "rb") as f:
            r = requests.post(f"{self.base}/object/{self.bucket}/{key}", data=f, timeout=600,
                              headers={**self.h, "x-upsert": "true",
                                       "Content-Type": content_type or "application/octet-stream"})
        r.raise_for_status()
        return key

    def _put_resumable(self, local: Path, key: str, content_type: str | None) -> str:
        """TUS resumable upload (Supabase requires 6 MB chunks). Used for renders and long audio."""
        import base64
        chunk = 6 * 1024 * 1024
        size = local.stat().st_size
        b64 = lambda s: base64.b64encode(s.encode()).decode()  # noqa: E731
        meta = ",".join([f"bucketName {b64(self.bucket)}", f"objectName {b64(key)}",
                         f"contentType {b64(content_type or 'application/octet-stream')}", f"cacheControl {b64('3600')}"])
        h = {**self.h, "Tus-Resumable": "1.0.0", "x-upsert": "true"}
        r = requests.post(f"{self.base}/upload/resumable", timeout=60,
                          headers={**h, "Upload-Length": str(size), "Upload-Metadata": meta})
        r.raise_for_status()
        url = r.headers["Location"]
        offset = 0
        with open(local, "rb") as f:
            while offset < size:
                f.seek(offset)
                data = f.read(chunk)
                r = requests.patch(url, data=data, timeout=600, headers={**h, "Upload-Offset": str(offset),
                                                                         "Content-Type": "application/offset+octet-stream"})
                if r.status_code == 409:  # offset mismatch after a dropped connection: ask the server and continue
                    offset = int(requests.head(url, headers=h, timeout=60).headers["Upload-Offset"])
                    continue
                r.raise_for_status()
                offset = int(r.headers["Upload-Offset"])
        return key

    def get(self, key: str, local: Path) -> Path:
        local.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(f"{self.base}/object/{self.bucket}/{key}", headers=self.h, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(local, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        return local

    def exists(self, key: str) -> bool:
        r = requests.head(f"{self.base}/object/{self.bucket}/{key}", headers=self.h, timeout=30)
        return r.status_code == 200

    def delete_prefix(self, prefix: str) -> None:
        r = requests.post(f"{self.base}/object/list/{self.bucket}", headers=self.h, json={"prefix": prefix, "limit": 1000}, timeout=60)
        names = [f"{prefix}/{o['name']}" for o in r.json()] if r.ok else []
        if names:
            requests.delete(f"{self.base}/object/{self.bucket}", headers=self.h, json={"prefixes": names}, timeout=60)


_store = None


def store():
    global _store
    if _store is None:
        _store = SupabaseStorage() if settings.storage_driver == "supabase" else LocalStorage(settings.local_storage_dir)
    return _store
