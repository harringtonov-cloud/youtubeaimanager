def test_health_is_public(anon_client):
    r = anon_client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] is True
    assert "redis" in body  # redis is down in tests -> degraded, but endpoint still answers


def test_api_requires_token(anon_client):
    assert anon_client.get("/api/overview").status_code == 401
    assert anon_client.get("/api/settings").status_code == 401
    r = anon_client.get("/api/overview", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_auth_check(client):
    assert client.get("/api/auth/check").json() == {"ok": True}


def test_overview_shape(client):
    r = client.get("/api/overview")
    assert r.status_code == 200
    d = r.json()
    for key in ("ai", "server", "youtube", "queue", "counts", "recent_errors", "recent_actions"):
        assert key in d
    assert d["youtube"]["connected"] is False
    assert d["ai"]["mode"] == "unconfigured"  # no LLM key in tests
    assert d["counts"]["videos_created"] == 0


def test_settings_roundtrip_and_validation(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["settings"]["auto_publish_enabled"] is False
    assert "LLM_API_KEY" in body["secrets"] and body["secrets"]["LLM_API_KEY"] is False

    r = client.put("/api/settings", json={"niche": "space facts", "publish_times": ["09:00", "18:30"]})
    assert r.status_code == 200
    s = r.json()["settings"]
    assert s["niche"] == "space facts" and s["videos_per_day"] == 2

    assert client.put("/api/settings", json={"publish_times": ["25:99"]}).status_code == 422
    assert client.put("/api/settings", json={"timezone": "Mars/Base"}).status_code == 422
    assert client.get("/api/settings").json()["settings"]["niche"] == "space facts"


def test_logs_endpoint(client):
    from app.events import log_event

    log_event("test", "hello world")
    log_event("test", "something broke", level="ERROR")
    r = client.get("/api/logs", params={"level": "ERROR"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["message"] == "something broke"


def test_video_listing_and_cancel(client, db):
    from app.models import Video

    v = Video(title="demo", status="planned")
    db.add(v)
    db.commit()
    r = client.get("/api/videos")
    assert r.json()["total"] == 1
    assert client.post(f"/api/videos/{v.id}/cancel").status_code == 200
    assert client.get(f"/api/videos/{v.id}").json()["status"] == "cancelled"
    assert client.get("/api/videos/9999").status_code == 404


def test_media_blocks_path_traversal(client):
    r = client.get("/api/media/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404)
