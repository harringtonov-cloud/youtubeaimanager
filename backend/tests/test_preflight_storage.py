from app.services import preflight


def _storage_check(db):
    return next(c for c in preflight.run_checks(db, live=False)["checks"] if c["id"] == "storage")


def test_local_storage_is_fine_off_cloud(db, monkeypatch):
    for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID"):
        monkeypatch.delenv(k, raising=False)
    c = _storage_check(db)
    assert c["ok"] and c["blocking"]


def test_local_storage_blocks_autopublish_on_railway(db, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    c = _storage_check(db)
    assert not c["ok"] and c["blocking"] and "s3" in c["detail"].lower()
    assert preflight.run_checks(db, live=False)["ready"] is False
