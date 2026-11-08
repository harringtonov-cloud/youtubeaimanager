from datetime import timedelta

import pytest

from app import redis_util, runner
from app.config import get_config
from app.models import FAILED, PLANNED, READY, JobRun, Video, utcnow
from app.stages import Deferred, PermanentError
from app.workers import tasks


@pytest.fixture(autouse=True)
def inline(monkeypatch):
    monkeypatch.setattr(get_config(), "queue_mode", "inline")
    monkeypatch.setattr(runner, "PERIODIC", [])  # no external calls in unit tests


def _fake_stages(monkeypatch, **overrides):
    calls = []

    def make(name):
        def fn(db, video):
            calls.append((name, video.id))
            if name in overrides:
                raise overrides[name]
        return fn

    monkeypatch.setattr(tasks, "_runners", lambda: {n: make(n) for n in ("script", "seo", "render", "thumbnail", "publish")})
    return calls


def _video(db, status=PLANNED, hours=1):
    v = Video(title="t", status=status, scheduled_at=utcnow() + timedelta(hours=hours))
    db.add(v)
    db.commit()
    return v


def test_enqueue_is_noop_without_broker():
    tasks.enqueue_stage(1, "script")  # would raise a connection error if it tried to reach Redis


def test_cycle_runs_all_stages_to_ready(db, monkeypatch):
    calls = _fake_stages(monkeypatch)
    v = _video(db)
    summary = runner.run_cycle(budget_s=60)
    db.expire_all()
    assert db.get(Video, v.id).status == READY  # publication is not due yet
    assert [c[0] for c in calls] == ["script", "seo", "render", "thumbnail"]
    assert summary["pipeline"]["ok"] == 4


def test_failing_stage_is_not_hammered_within_one_cycle(db, monkeypatch):
    calls = _fake_stages(monkeypatch, script=RuntimeError("boom"))
    v = _video(db)
    summary = runner.run_cycle(budget_s=60)
    db.expire_all()
    v = db.get(Video, v.id)
    assert len(calls) == 1 and v.status == PLANNED and v.attempts == 1 and "boom" in v.error
    assert summary["pipeline"]["retry"] == 1


def test_permanent_failure_marks_failed(db, monkeypatch):
    _fake_stages(monkeypatch, script=PermanentError("bad key"))
    v = _video(db)
    runner.run_cycle(budget_s=60)
    db.expire_all()
    assert db.get(Video, v.id).status == FAILED


def test_deferred_keeps_state(db, monkeypatch):
    _fake_stages(monkeypatch, script=Deferred("paused"))
    v = _video(db)
    summary = runner.run_cycle(budget_s=60)
    db.expire_all()
    v = db.get(Video, v.id)
    assert v.status == PLANNED and v.attempts == 0 and v.processing_stage is None
    assert summary["pipeline"]["deferred"] == 1


def test_earliest_slot_is_produced_first(db, monkeypatch):
    calls = _fake_stages(monkeypatch)
    late = _video(db, hours=30)
    soon = _video(db, hours=2)
    runner.run_cycle(budget_s=60)
    assert calls[0] == ("script", soon.id) and late.id != soon.id


def test_zero_budget_does_nothing(db, monkeypatch):
    calls = _fake_stages(monkeypatch)
    _video(db)
    runner.run_cycle(budget_s=0)
    assert calls == []


def test_runner_signal_drives_worker_status(db, monkeypatch):
    _fake_stages(monkeypatch)
    assert redis_util.worker_heartbeat_age() is None
    runner.run_cycle(budget_s=10)
    age = redis_util.worker_heartbeat_age()
    assert age is not None and age < redis_util.worker_alive_limit()
    assert redis_util.redis_ok() is True and redis_util.queue_length() is None
    db.expire_all()
    run = db.query(JobRun).filter(JobRun.name == "runner").one()
    assert run.status == "ok" and run.finished_at is not None


def test_overview_reports_worker_online_in_inline_mode(client, monkeypatch):
    _fake_stages(monkeypatch)
    runner.run_cycle(budget_s=10)
    d = client.get("/api/overview").json()
    assert d["server"]["worker_online"] is True and d["server"]["redis"] is True


def test_dashboard_run_job_inline(client, monkeypatch):
    called = []
    monkeypatch.setattr(tasks, "generate_ideas_task", lambda: called.append(1))
    r = client.post("/api/jobs/run/generate_ideas")
    assert r.status_code == 200 and r.json()["mode"] == "inline"
    assert called == [1]  # background task ran after the response


def test_local_storage_blocks_autopublish_in_inline_mode(db):
    from app.services import preflight

    c = next(x for x in preflight.run_checks(db, live=False)["checks"] if x["id"] == "storage")
    assert not c["ok"] and "s3" in c["detail"].lower()
