"""First comment: scheduled by the publisher, posted later with retries, never twice."""
from datetime import timedelta

import pytest

from app.models import Publication, Video, utcnow
from app.services import first_comment as fc
from app.services import youtube
from app.services.youtube import QuotaExceeded, YouTubeError
from app.settings_store import update_settings


@pytest.fixture(autouse=True)
def _connected(db, monkeypatch):
    monkeypatch.setattr(youtube, "is_connected", lambda d: True)
    update_settings(db, {"first_comment_enabled": True})


def _published(db, *, seo_extra=None, age_min=10):
    v = Video(title="t", status="published",
              seo={"title": "T", "comment": "А вы бы согласились?", **(seo_extra or {})})
    db.add(v)
    db.commit()
    db.add(Publication(video_id=v.id, status="published", youtube_video_id=f"YT{v.id}",
                       published_at=utcnow() - timedelta(minutes=age_min)))
    db.commit()
    return v


def _state(db, v):
    db.refresh(v)
    return fc.state_of(v)


def test_schedule_sets_pending_with_delay(db):
    v = Video(title="t", seo={"comment": "Вопрос?"})
    db.add(v)
    db.commit()
    now = utcnow()
    fc.schedule(v, now)
    st = fc.state_of(v)
    assert st["status"] == fc.PENDING and st["attempts"] == 0
    assert fc._parse(st["next_at"]) >= now + fc.FIRST_DELAY - timedelta(seconds=1)
    fc.schedule(v, now + timedelta(hours=1))  # already scheduled: untouched
    assert fc.state_of(v)["next_at"] == st["next_at"]


def test_schedule_without_comment_text_does_nothing(db):
    v = Video(title="t", seo={"comment": ""})
    db.add(v)
    db.commit()
    fc.schedule(v)
    assert fc.state_of(v) == {}


def test_not_due_before_delay(db, monkeypatch):
    v = _published(db)
    fc.schedule(v, utcnow())  # next_at = now + 3 min
    db.commit()
    monkeypatch.setattr(youtube, "post_comment", lambda *a, **k: pytest.fail("posted too early"))
    assert fc.process_pending(db) == {}


def test_posts_when_due_and_only_once(db, monkeypatch):
    v = _published(db)
    fc.schedule(v, utcnow() - timedelta(minutes=10))
    db.commit()
    calls = []
    monkeypatch.setattr(youtube, "find_own_comment", lambda d, vid, t: None)
    monkeypatch.setattr(youtube, "post_comment", lambda d, vid, t: calls.append((vid, t)) or "CID1")
    assert fc.process_pending(db) == {"posted": 1}
    assert fc.process_pending(db) == {}  # final state: nothing more to do
    assert calls == [(f"YT{v.id}", "А вы бы согласились?")]
    st = _state(db, v)
    assert st["status"] == fc.POSTED and st["id"] == "CID1"


def test_existing_own_comment_is_adopted_not_duplicated(db, monkeypatch):
    v = _published(db)
    fc.schedule(v, utcnow() - timedelta(minutes=10))
    db.commit()
    monkeypatch.setattr(youtube, "find_own_comment", lambda d, vid, t: "OLD")
    monkeypatch.setattr(youtube, "post_comment", lambda *a, **k: pytest.fail("duplicate insert"))
    assert fc.process_pending(db) == {"posted": 1}
    assert _state(db, v)["id"] == "OLD"


def test_legacy_video_with_comment_tried_gets_one_safe_retry(db, monkeypatch):
    v = _published(db, seo_extra={"comment_tried": True})  # old code: tried once, outcome unknown
    monkeypatch.setattr(youtube, "find_own_comment", lambda d, vid, t: None)
    monkeypatch.setattr(youtube, "post_comment", lambda d, vid, t: "NEW")
    assert fc.process_pending(db) == {"posted": 1}
    assert _state(db, v)["status"] == fc.POSTED


def test_old_video_without_state_and_flag_is_left_alone(db, monkeypatch):
    _published(db)
    monkeypatch.setattr(youtube, "post_comment", lambda *a, **k: pytest.fail("must not post"))
    assert fc.process_pending(db) == {}


def test_transient_error_backs_off_then_succeeds(db, monkeypatch):
    v = _published(db)
    fc.schedule(v, utcnow() - timedelta(minutes=10))
    db.commit()
    monkeypatch.setattr(youtube, "find_own_comment", lambda d, vid, t: None)

    def boom(*a, **k):
        raise YouTubeError("YouTube API 404 videoNotFound: processing", 404, "videoNotFound")

    monkeypatch.setattr(youtube, "post_comment", boom)
    assert fc.process_pending(db) == {"retry": 1}
    st = _state(db, v)
    assert st["status"] == fc.PENDING and st["attempts"] == 1 and "videoNotFound" in st["error"]
    assert fc.process_pending(db) == {}  # back-off: not due yet

    st["next_at"] = (utcnow() - timedelta(minutes=1)).isoformat()  # time passes
    v.seo = {**v.seo, "comment_state": st}
    db.commit()
    monkeypatch.setattr(youtube, "post_comment", lambda d, vid, t: "CID2")
    assert fc.process_pending(db) == {"posted": 1}


def test_gives_up_after_max_attempts(db, monkeypatch):
    v = _published(db)
    fc.schedule(v, utcnow() - timedelta(minutes=10))
    db.commit()
    monkeypatch.setattr(youtube, "find_own_comment", lambda d, vid, t: None)
    monkeypatch.setattr(youtube, "post_comment",
                        lambda *a, **k: (_ for _ in ()).throw(YouTubeError("x", 403, "forbidden")))
    last = None
    for _ in range(fc.MAX_ATTEMPTS):
        st = _state(db, v)
        st["next_at"] = (utcnow() - timedelta(minutes=1)).isoformat()
        v.seo = {**v.seo, "comment_state": st}
        db.commit()
        last = fc.process_pending(db)
    assert last == {"failed": 1}
    assert _state(db, v)["status"] == fc.FAILED


def test_comments_disabled_is_final_skip(db, monkeypatch):
    v = _published(db)
    fc.schedule(v, utcnow() - timedelta(minutes=10))
    db.commit()
    monkeypatch.setattr(youtube, "find_own_comment", lambda d, vid, t: None)
    monkeypatch.setattr(youtube, "post_comment",
                        lambda *a, **k: (_ for _ in ()).throw(YouTubeError("off", 403, "commentsDisabled")))
    assert fc.process_pending(db) == {"skipped": 1}
    assert _state(db, v)["status"] == fc.SKIPPED


def test_missing_scope_reports_reconnect_and_keeps_attempts(db, monkeypatch):
    v = _published(db)
    fc.schedule(v, utcnow() - timedelta(minutes=10))
    db.commit()
    monkeypatch.setattr(youtube, "find_own_comment", lambda d, vid, t: None)
    monkeypatch.setattr(youtube, "post_comment", lambda *a, **k: (_ for _ in ()).throw(
        YouTubeError("Request had insufficient authentication scopes.", 403, "insufficientPermissions")))
    assert fc.process_pending(db) == {"reauth": 1}
    st = _state(db, v)
    assert st["status"] == fc.PENDING and st["attempts"] == 0 and "Подключить" in st["error"]


def test_quota_postpones_without_blocking_publishing(db, monkeypatch):
    v = _published(db)
    fc.schedule(v, utcnow() - timedelta(minutes=10))
    db.commit()
    monkeypatch.setattr(youtube, "find_own_comment", lambda d, vid, t: None)
    monkeypatch.setattr(youtube, "post_comment",
                        lambda *a, **k: (_ for _ in ()).throw(QuotaExceeded("quota", 403, "quotaExceeded")))
    monkeypatch.setattr(youtube, "block_for_quota", lambda *a, **k: pytest.fail("comments must not block uploads"))
    assert fc.process_pending(db) == {"quota": 1}
    assert _state(db, v)["attempts"] == 0


def test_disabled_setting_posts_nothing(db, monkeypatch):
    v = _published(db)
    fc.schedule(v, utcnow() - timedelta(minutes=10))
    db.commit()
    update_settings(db, {"first_comment_enabled": False})
    monkeypatch.setattr(youtube, "post_comment", lambda *a, **k: pytest.fail("disabled"))
    assert fc.process_pending(db) == {}


def test_stale_claim_is_retaken_fresh_claim_is_not(db, monkeypatch):
    v = _published(db)
    monkeypatch.setattr(youtube, "find_own_comment", lambda d, vid, t: None)
    monkeypatch.setattr(youtube, "post_comment", lambda d, vid, t: "CID3")
    v.seo = {**v.seo, "comment_state": {"status": fc.POSTING, "attempts": 0,
                                        "claimed_at": utcnow().isoformat()}}
    db.commit()
    assert fc.process_pending(db) == {}  # another worker is on it
    v.seo = {**v.seo, "comment_state": {"status": fc.POSTING, "attempts": 0,
                                        "claimed_at": (utcnow() - timedelta(minutes=30)).isoformat()}}
    db.commit()
    assert fc.process_pending(db) == {"posted": 1}


def test_classify_comment_error():
    c = youtube.classify_comment_error
    assert c(YouTubeError("m", 403, "insufficientPermissions")) == "reauth"
    assert c(YouTubeError("Request had insufficient authentication scopes.", 403, "PERMISSION_DENIED")) == "reauth"
    assert c(YouTubeError("m", 403, "commentsDisabled")) == "skip"
    assert c(YouTubeError("m", 400, "badRequest")) == "skip"
    assert c(YouTubeError("m", 404, "videoNotFound")) == "retry"
    assert c(YouTubeError("m", 403, "forbidden")) == "retry"
    assert c(YouTubeError("m", 503, "")) == "retry"


def test_comment_scope_is_requested():
    assert youtube.COMMENT_SCOPE in youtube.SCOPES
