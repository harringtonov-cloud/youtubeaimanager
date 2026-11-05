from urllib.parse import parse_qs, urlparse

from app.crypto import decrypt
from app.models import Idea, YouTubeAccount
from app.services import youtube


def test_youtube_status_and_connect(client):
    st = client.get("/api/youtube/status").json()
    assert st["connected"] is False and st["oauth_configured"] is True
    assert st["redirect_uri"].endswith("/api/youtube/callback")
    url = client.post("/api/youtube/connect").json()["auth_url"]
    q = parse_qs(urlparse(url).query)
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == ["test-client-id"]
    assert "youtube.upload" in q["scope"][0] and "yt-analytics.readonly" in q["scope"][0]


def test_callback_rejects_unknown_state(anon_client):
    r = anon_client.get("/api/youtube/callback", params={"code": "x", "state": "nope"}, follow_redirects=False)
    assert r.status_code in (302, 307) and "error=" in r.headers["location"]


def test_callback_requires_all_scopes(client, anon_client, monkeypatch):
    url = client.post("/api/youtube/connect").json()["auth_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    monkeypatch.setattr(youtube, "_post_token", lambda data: {
        "access_token": "at", "refresh_token": "rt", "scope": "https://www.googleapis.com/auth/youtube.upload"})
    r = anon_client.get("/api/youtube/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert client.get("/api/youtube/status").json()["connected"] is False


def test_callback_success_stores_encrypted_token(client, anon_client, db, monkeypatch):
    url = client.post("/api/youtube/connect").json()["auth_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    seen = {}

    def fake_token(data):
        seen.update(data)
        return {"access_token": "at", "refresh_token": "rt-secret", "scope": " ".join(youtube.SCOPES), "expires_in": 3600}

    monkeypatch.setattr(youtube, "_post_token", fake_token)
    monkeypatch.setattr(youtube, "_fetch_channel", lambda token: {
        "id": "UC123", "snippet": {"title": "My Channel", "thumbnails": {}},
        "statistics": {"subscriberCount": "5", "videoCount": "2"},
        "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}}})
    r = anon_client.get("/api/youtube/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert "connected=1" in r.headers["location"]
    assert seen["code_verifier"] and seen["grant_type"] == "authorization_code"  # PKCE verifier was sent
    st = client.get("/api/youtube/status").json()
    assert st["connected"] and st["channel_title"] == "My Channel" and st["subscriber_count"] == 5
    acct = db.query(YouTubeAccount).one()
    assert acct.refresh_token_enc != "rt-secret" and decrypt(acct.refresh_token_enc) == "rt-secret"
    # the one-time state cannot be replayed
    r = anon_client.get("/api/youtube/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert "rt-secret" not in client.get("/api/youtube/status").text


def test_auto_publish_gate_blocks_until_preflight_passes(client):
    r = client.put("/api/settings", json={"auto_publish_enabled": True})
    assert r.status_code == 409
    report = r.json()["detail"]["report"]
    assert report["ready"] is False
    failed = {c["id"] for c in report["checks"] if c["blocking"] and not c["ok"]}
    assert {"llm", "youtube"} <= failed
    assert client.get("/api/settings").json()["settings"]["auto_publish_enabled"] is False


def test_preflight_endpoint(client):
    r = client.get("/api/preflight", params={"live": "false"})
    assert r.status_code == 200 and {"ready", "checks"} <= set(r.json())


def test_ideas_crud_and_dedupe(client, db):
    r = client.post("/api/ideas", json={"topic": "Why honey never spoils", "keywords": ["honey"]})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["source"] == "manual"
    iid = r.json()["id"]
    assert client.post("/api/ideas", json={"topic": "honey never spoils, why?"}).status_code == 409
    assert client.post("/api/ideas", json={"topic": "x"}).status_code == 422
    assert client.patch(f"/api/ideas/{iid}", json={"status": "rejected"}).json()["status"] == "rejected"
    assert client.patch(f"/api/ideas/{iid}", json={"status": "bogus"}).status_code == 422
    items = client.get("/api/ideas", params={"status": "rejected"}).json()["items"]
    assert [i["id"] for i in items] == [iid]
    assert client.get("/api/director/state").status_code == 200
    assert client.get("/api/analytics/summary").json()["videos_with_data"] == 0
    assert client.get("/api/analytics/insights").json()["items"] == []
    assert client.post("/api/jobs/run/nonexistent").status_code == 404
