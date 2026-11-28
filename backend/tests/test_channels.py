"""Several YouTube channels in one installation: every channel sees only its own data."""
from sqlalchemy import select

from app import scope
from app.models import Idea, Setting, Video, YouTubeAccount
from app.services.text_utils import fingerprint
from app.settings_store import get_flag, get_settings, set_flag, update_settings


def _accounts(db, n=2):
    ids = []
    for i in range(n):
        a = YouTubeAccount(channel_id=f"UC{i}", channel_title=f"Channel {i}", refresh_token_enc="x")
        db.add(a)
        db.commit()
        ids.append(a.id)
    return ids


def test_rows_are_stamped_and_filtered_per_channel(db):
    a, b = _accounts(db)
    sa, sb = scope.Scope(a, legacy=True), scope.Scope(b)

    with scope.use(sa):
        db.add(Idea(topic="topic of A", fingerprint="fa", status="approved"))
        db.commit()
    with scope.use(sb):
        db.add(Idea(topic="topic of B", fingerprint="fb", status="approved"))
        db.commit()

    with scope.use(sa):
        assert [i.topic for i in db.execute(select(Idea)).scalars()] == ["topic of A"]
    with scope.use(sb):
        assert [i.topic for i in db.execute(select(Idea)).scalars()] == ["topic of B"]
    # no scope: everything is visible (scripts, tests, a database without channels)
    assert db.execute(select(Idea)).scalars().all().__len__() == 2


def test_legacy_channel_owns_rows_without_owner(db):
    a, b = _accounts(db)
    db.add(Idea(topic="old idea", fingerprint="old", status="approved"))  # created before multi-channel: no owner
    db.commit()
    with scope.use(scope.Scope(a, legacy=True)):
        assert db.execute(select(Idea)).scalars().all()[0].topic == "old idea"
    with scope.use(scope.Scope(b)):
        assert db.execute(select(Idea)).scalars().all() == []


def test_settings_and_flags_are_per_channel_but_legacy_keys_are_unchanged(db):
    a, b = _accounts(db)
    with scope.use(scope.Scope(a, legacy=True)):
        update_settings(db, {"niche": "cats"})
        set_flag(db, "x", 1)
    with scope.use(scope.Scope(b)):
        assert get_settings(db).niche != "cats"
        assert get_flag(db, "x") is None
        update_settings(db, {"niche": "space"})
        set_flag(db, "x", 2)
    with scope.use(scope.Scope(a, legacy=True)):
        assert get_settings(db).niche == "cats"
        assert get_flag(db, "x") == 1
    keys = {k for (k,) in db.execute(select(Setting.key)).all()}
    assert "app_settings" in keys and "flag:x" in keys  # the original channel keeps the original keys
    assert f"a{b}:app_settings" in keys and f"a{b}:flag:x" in keys


def test_same_topic_can_exist_in_two_channels(db):
    a, b = _accounts(db)
    fp = fingerprint("Why do cats purr")
    with scope.use(scope.Scope(a, legacy=True)):
        db.add(Idea(topic="Why do cats purr", fingerprint=scope.salt_fingerprint(fp), status="approved"))
        db.commit()
    with scope.use(scope.Scope(b)):
        assert scope.salt_fingerprint(fp) != fp and len(scope.salt_fingerprint(fp)) == 64
        db.add(Idea(topic="Why do cats purr", fingerprint=scope.salt_fingerprint(fp), status="approved"))
        db.commit()  # would violate the unique fingerprint if it was not salted


def test_account_list_is_global_but_account_row_is_scoped(db):
    a, b = _accounts(db)
    with scope.use(scope.Scope(b)):
        rows = db.execute(select(YouTubeAccount)).scalars().all()
        assert [r.id for r in rows] == [b]


def test_resolve_picks_requested_or_oldest(db):
    a, b = _accounts(db)
    assert scope.resolve(db, None) == scope.Scope(a, legacy=True)
    assert scope.resolve(db, str(b)) == scope.Scope(b, legacy=False)
    assert scope.resolve(db, "999") == scope.Scope(a, legacy=True)  # unknown id -> original channel
    assert scope.resolve(db, "abc") == scope.Scope(a, legacy=True)


def test_resolve_without_channels_is_none(db):
    assert scope.resolve(db, "1") is None


def test_api_channel_header_switches_data(client, db):
    a, b = _accounts(db)
    with scope.use(scope.Scope(a, legacy=True)):
        db.add(Video(title="video of A", status="planned"))
        db.commit()
    with scope.use(scope.Scope(b)):
        db.add(Video(title="video of B", status="planned"))
        db.commit()

    r = client.get("/api/channels")
    assert r.status_code == 200
    assert [c["id"] for c in r.json()["channels"]] == [a, b]
    assert r.json()["current"] == a  # no header: the original channel

    assert client.get("/api/videos").json()["total"] == 1  # the counter is per channel too
    assert client.get("/api/videos", headers={"X-Channel": str(b)}).json()["total"] == 1
    titles_a = {v["title"] for v in client.get("/api/videos").json()["items"]}
    titles_b = {v["title"] for v in client.get("/api/videos", headers={"X-Channel": str(b)}).json()["items"]}
    assert titles_a == {"video of A"}
    assert titles_b == {"video of B"}


def test_worker_signal_is_global_not_per_channel(db):
    """The runner is one process for all channels: a cycle recorded for channel A counts for channel B too."""
    from app.models import JobRun
    from app.redis_util import _runner_age_from_db

    a, b = _accounts(db)
    with scope.use(scope.Scope(a, legacy=True)):
        db.add(JobRun(name="runner", status="ok"))
        db.commit()
    with scope.use(scope.Scope(b)):
        assert _runner_age_from_db() is not None
