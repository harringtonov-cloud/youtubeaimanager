"""Resumable-upload protocol and API error classification, with scripted HTTP responses."""
import httpx
import pytest

from app.services import youtube
from app.stages import PermanentError


def _r(status, **kw):
    return httpx.Response(status, request=httpx.Request("PUT", "http://x"), **kw)


@pytest.fixture
def vfile(tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"0123456789")  # 10 bytes
    return p


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(youtube, "access_token", lambda db, force_refresh=False: "tok")
    monkeypatch.setattr(youtube.time, "sleep", lambda s: None)


def test_upload_single_chunk(db, vfile, monkeypatch):
    calls = {}

    def post(url, params=None, headers=None, content=None, timeout=None):
        calls["init"] = (params, headers, content)
        return _r(200, headers={"Location": "https://upload.example/session1"})

    def put(url, content=None, headers=None, timeout=None):
        calls["put"] = (url, headers, content)
        return _r(200, json={"id": "VID1"})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "put", put)
    vid = youtube.upload_video(db, vfile, {"title": "T"}, {"privacyStatus": "public", "containsSyntheticMedia": True})
    assert vid == "VID1"
    assert calls["init"][0]["uploadType"] == "resumable" and calls["init"][1]["X-Upload-Content-Length"] == "10"
    assert calls["put"][1]["Content-Range"] == "bytes 0-9/10" and calls["put"][2] == b"0123456789"


def test_upload_resumes_after_308_and_server_error(db, vfile, monkeypatch):
    monkeypatch.setattr(youtube, "CHUNK", 4)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _r(200, headers={"Location": "https://u/s"}))
    script = [
        _r(308, headers={"Range": "bytes=0-3"}),   # first chunk accepted
        _r(503),                                    # second chunk fails...
        _r(308, headers={"Range": "bytes=0-3"}),   # ...status query says 4 bytes are stored
        _r(308, headers={"Range": "bytes=0-7"}),   # chunk 2 re-sent and accepted
        _r(200, json={"id": "VID2"}),               # last chunk completes
    ]
    ranges = []

    def put(url, content=None, headers=None, timeout=None):
        ranges.append(headers["Content-Range"])
        return script.pop(0)

    monkeypatch.setattr(httpx, "put", put)
    assert youtube.upload_video(db, vfile, {"title": "T"}, {}) == "VID2"
    assert ranges == ["bytes 0-3/10", "bytes 4-7/10", "bytes */10", "bytes 4-7/10", "bytes 8-9/10"]


def test_upload_retries_without_synthetic_flag(db, vfile, monkeypatch):
    bodies = []

    def post(url, params=None, headers=None, content=None, timeout=None):
        bodies.append(content)
        if len(bodies) == 1:
            return _r(400, json={"error": {"message": "Unknown field containsSyntheticMedia", "errors": [{"reason": "invalidArgument"}]}})
        return _r(200, headers={"Location": "https://u/s"})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "put", lambda *a, **k: _r(200, json={"id": "V3"}))
    assert youtube.upload_video(db, vfile, {"title": "T"}, {"containsSyntheticMedia": True}) == "V3"
    assert "containsSyntheticMedia" in bodies[0] and "containsSyntheticMedia" not in bodies[1]


def test_upload_empty_file_is_permanent(db, tmp_path):
    p = tmp_path / "empty.mp4"
    p.write_bytes(b"")
    with pytest.raises(PermanentError):
        youtube.upload_video(db, p, {}, {})


def test_error_classification():
    with pytest.raises(youtube.QuotaExceeded):
        youtube._raise_for(_r(403, json={"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}}))
    with pytest.raises(youtube.YouTubeError) as ei:
        youtube._raise_for(_r(503, json={"error": {"message": "backend"}}))
    assert ei.value.transient
    with pytest.raises(youtube.YouTubeError) as ei:
        youtube._raise_for(_r(403, json={"error": {"message": "forbidden", "errors": [{"reason": "forbidden"}]}}))
    assert not ei.value.transient and ei.value.reason == "forbidden"
    youtube._raise_for(_r(200, json={}))  # no error
