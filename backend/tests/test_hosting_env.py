from app.config import Config
from app.services import preflight


def test_public_url_from_render(monkeypatch):
    monkeypatch.delenv("BACKEND_PUBLIC_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://yam-api.onrender.com/")
    c = Config()
    assert c.backend_public_url == "https://yam-api.onrender.com"
    assert c.oauth_redirect_uri == "https://yam-api.onrender.com/api/youtube/callback"


def test_explicit_public_url_wins(monkeypatch):
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://my.domain")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://yam-api.onrender.com")
    assert Config().backend_public_url == "https://my.domain"


def test_render_blocks_local_storage(db, monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    c = next(x for x in preflight.run_checks(db, live=False)["checks"] if x["id"] == "storage")
    assert not c["ok"] and c["blocking"]
