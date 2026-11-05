from datetime import timedelta

from app import stages
from app.models import FAILED, PLANNED, READY, SCRIPTED, Video, utcnow


def _video(db, status=PLANNED):
    v = Video(title="t", status=status)
    db.add(v)
    db.commit()
    return v


def test_acquire_is_exclusive(db):
    v = _video(db)
    first = stages.acquire(db, v.id, "script")
    assert first is not None and first.processing_stage == "script"
    assert stages.acquire(db, v.id, "script") is None  # second worker is rejected


def test_acquire_requires_matching_status(db):
    v = _video(db, status=SCRIPTED)
    assert stages.acquire(db, v.id, "script") is None
    assert stages.acquire(db, v.id, "seo") is not None


def test_stale_lock_can_be_taken_over(db):
    v = _video(db)
    stages.acquire(db, v.id, "script")
    v = db.get(Video, v.id)
    v.locked_at = utcnow() - timedelta(minutes=stages.LOCK_TTL_MIN["script"] + 1)
    db.commit()
    assert stages.acquire(db, v.id, "script") is not None  # crashed worker's lock expired


def test_complete_advances_and_resets(db):
    v = _video(db)
    v = stages.acquire(db, v.id, "script")
    v.attempts = 2
    v.error = "old"
    stages.complete(db, v, "script")
    v = db.get(Video, v.id)
    assert v.status == SCRIPTED and v.processing_stage is None and v.attempts == 0 and v.error is None


def test_failure_retries_then_fails(db):
    v = _video(db)
    v = stages.acquire(db, v.id, "script")
    assert stages.register_failure(db, v, "script", RuntimeError("boom"), max_attempts=2) == "retry"
    assert db.get(Video, v.id).status == PLANNED
    v = stages.acquire(db, v.id, "script")
    assert stages.register_failure(db, v, "script", RuntimeError("boom"), max_attempts=2) == "failed"
    v = db.get(Video, v.id)
    assert v.status == FAILED and v.failed_stage == "script" and "boom" in v.error


def test_permanent_failure_fails_immediately(db):
    v = _video(db)
    v = stages.acquire(db, v.id, "script")
    assert stages.register_failure(db, v, "script", stages.PermanentError("bad"), 5, permanent=True) == "failed"


def test_reset_for_retry_returns_to_failed_stage(db):
    v = _video(db, status=READY)
    v.status = FAILED
    v.failed_stage = "render"
    db.commit()
    stage = stages.reset_for_retry(db, v)
    v = db.get(Video, v.id)
    assert stage == "render" and v.status == "seo_ready" and v.error is None


def test_backoff_grows_and_caps():
    assert stages.backoff_seconds(1) == 60
    assert stages.backoff_seconds(2) == 120
    assert stages.backoff_seconds(20) == 1800


def test_release_keeps_status(db):
    v = _video(db)
    v = stages.acquire(db, v.id, "script")
    stages.release(db, v)
    v = db.get(Video, v.id)
    assert v.status == PLANNED and v.processing_stage is None and v.attempts == 0
