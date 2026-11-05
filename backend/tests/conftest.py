import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="yam-test-")
os.environ.update(
    DATABASE_URL=f"sqlite:///{_TMP}/test.sqlite",
    ADMIN_TOKEN="test-token",
    SECRET_KEY="test-secret-key-0123456789",
    REDIS_URL="redis://127.0.0.1:6399/0",  # nothing listens here: redis helpers must degrade gracefully
    DATA_DIR=_TMP,
    STORAGE_BACKEND="local",
    LLM_API_KEY="",
    GOOGLE_CLIENT_ID="test-client-id",
    GOOGLE_CLIENT_SECRET="test-client-secret",
    BACKEND_PUBLIC_URL="http://testserver",
    FRONTEND_URL="http://localhost:3000",
    TIMEZONE="UTC",
)

import pytest  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, headers={"Authorization": "Bearer test-token"})


@pytest.fixture
def anon_client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)
