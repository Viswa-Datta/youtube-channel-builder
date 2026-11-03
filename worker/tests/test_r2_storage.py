"""R2 driver against a local S3-compatible server (moto). Skipped when moto is not installed."""
import os

import pytest

moto_server = pytest.importorskip("moto.server")


@pytest.fixture()
def r2(tmp_path, monkeypatch):
    srv = moto_server.ThreadedMotoServer(port=0, verbose=False)
    srv.start()
    host, port = srv.get_host_and_port()
    from ycb import storage
    from ycb.config import settings
    for k, v in {"r2_endpoint": f"http://{host}:{port}", "r2_account_id": "acct", "r2_access_key_id": "k",
                 "r2_secret_access_key": "s", "r2_bucket": "ycb-media"}.items():
        monkeypatch.setattr(settings, k, v)
    st = storage.R2Storage()
    import boto3
    boto3.client("s3", endpoint_url=settings.r2_endpoint, region_name="us-east-1", aws_access_key_id="k",
                 aws_secret_access_key="s").create_bucket(Bucket="ycb-media")  # the fake server rejects region "auto" for bucket creation
    yield st
    srv.stop()


def test_put_get_exists_delete(r2, tmp_path):
    small = tmp_path / "a.txt"
    small.write_text("hello")
    big = tmp_path / "big.mp4"
    big.write_bytes(os.urandom(70 << 20))  # over the 64 MB multipart threshold
    r2.put(small, "projects/p1/audio/a.txt", "text/plain")
    r2.put(big, "projects/p1/renders/v-1.mp4", "video/mp4")
    assert r2.exists("projects/p1/audio/a.txt") and not r2.exists("projects/p1/nope")
    out = r2.get("projects/p1/renders/v-1.mp4", tmp_path / "dl" / "v.mp4")
    assert out.read_bytes() == big.read_bytes()
    assert r2.s3.head_object(Bucket="ycb-media", Key="projects/p1/renders/v-1.mp4")["ContentType"] == "video/mp4"
    r2.delete_prefix("projects/p1")
    assert not r2.exists("projects/p1/audio/a.txt") and not r2.exists("projects/p1/renders/v-1.mp4")


def test_missing_keys_are_listed(monkeypatch):
    from ycb import storage
    from ycb.config import MissingConfig, settings
    monkeypatch.setattr(settings, "r2_secret_access_key", None)
    with pytest.raises(MissingConfig):
        storage.R2Storage()
