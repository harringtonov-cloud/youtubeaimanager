"""Serverless runner: executes one full pipeline cycle in-process, with no Redis and no Celery.

Intended for a scheduled host (GitHub Actions cron every 30 minutes) when QUEUE_MODE=inline.
One cycle:
  1. periodic jobs that are due (YouTube check, analytics sync, learning, housekeeping)
  2. planning: fill free publishing slots with ideas (the same code as the Celery "tick")
  3. pipeline: publish what is due, then push every unfinished video through its next stages
     (script -> SEO -> render -> thumbnail) until the time budget is used up.

All state lives in Postgres and stage locks, so overlapping or killed runs are safe: a killed
run leaves a stage lock that simply expires (see stages.LOCK_TTL_MIN) and the next run continues.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import select

from .config import get_config
from .db import SessionLocal
from .events import log_event, setup_logging
from .models import PLANNED, PUBLISHING, RENDERED, SCRIPTED, SEO_READY, JobRun, Video, utcnow
from .settings_store import get_settings
from .stages import (
    Deferred,
    PermanentError,
    acquire,
    complete,
    is_locked,
    register_failure,
    release,
    status_to_stage,
)

PRODUCTION_STATUSES = [PLANNED, SCRIPTED, SEO_READY, RENDERED]

# (job name, minimum interval, attribute of app.workers.tasks)
PERIODIC: list[tuple[str, timedelta, str]] = [
    ("verify_youtube", timedelta(hours=6), "verify_youtube_task"),
    ("sync_analytics", timedelta(hours=3), "sync_analytics_task"),
    ("learn", timedelta(hours=22), "learn_task"),
    ("housekeeping", timedelta(hours=24), "housekeeping_task"),
]


def migrate() -> None:
    """Apply Alembic migrations (guarded by a Postgres advisory lock, safe to run concurrently)."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    command.upgrade(AlembicConfig("alembic.ini"), "head")


def execute_stage(video_id: int, stage: str) -> str:
    """Run one stage in-process. Returns ok | skipped | deferred | retry | failed."""
    from .workers import tasks

    with SessionLocal() as db:
        max_attempts = get_settings(db).max_attempts
        video = acquire(db, video_id, stage)
        if video is None:
            return "skipped"
        log_event("pipeline", f"stage '{stage}' started", video_id=video_id)
        try:
            tasks._runners()[stage](db, video)
            db.refresh(video)
            complete(db, video, stage)
        except Deferred as e:
            release(db, video)
            log_event("pipeline", f"stage '{stage}' deferred: {e}", video_id=video_id)
            return "deferred"
        except PermanentError as e:
            register_failure(db, video, stage, e, max_attempts, permanent=True)
            log_event("pipeline", f"stage '{stage}' failed permanently: {e}", level="ERROR", video_id=video_id)
            return "failed"
        except Exception as e:
            outcome = register_failure(db, video, stage, e, max_attempts)
            attempts = db.get(Video, video_id).attempts
            log_event(
                "pipeline",
                f"stage '{stage}' error (attempt {attempts}/{max_attempts}): {e}",
                level="ERROR" if outcome == "failed" else "WARNING",
                video_id=video_id,
            )
            return "failed" if outcome == "failed" else "retry"
    log_event("pipeline", f"stage '{stage}' done", video_id=video_id)
    return "ok"


def next_job(skip: set[int]) -> Optional[tuple[int, str]]:
    """The next (video_id, stage) to run: due publications first, then production by slot order."""
    from .services import publisher

    with SessionLocal() as db:
        for vid in publisher.due_videos(db):
            if vid not in skip:
                return vid, "publish"
        now = utcnow()
        rows = db.execute(
            select(Video)
            .where(Video.status.in_(PRODUCTION_STATUSES + [PUBLISHING]))
            .order_by(Video.scheduled_at.asc(), Video.id.asc())
        ).scalars().all()
        for v in rows:
            if v.id in skip or is_locked(v, now):
                continue
            stage = status_to_stage(v.status)
            if stage:
                return v.id, stage
    return None


def process_videos(deadline: float) -> dict[str, int]:
    counts: Counter[str] = Counter()
    skip: set[int] = set()  # videos that failed/deferred this cycle: the next cycle is their retry
    while time.monotonic() < deadline:
        job = next_job(skip)
        if job is None:
            break
        video_id, stage = job
        outcome = execute_stage(video_id, stage)
        counts[outcome] += 1
        if outcome != "ok":
            skip.add(video_id)
    if time.monotonic() >= deadline:
        counts["budget_exhausted"] += 1
    return dict(counts)


def _periodic_due(name: str, every: timedelta) -> bool:
    with SessionLocal() as db:
        last = db.execute(
            select(JobRun.started_at)
            .where(JobRun.name == name, JobRun.status == "ok")
            .order_by(JobRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    return last is None or utcnow() - last >= every


def run_cycle(budget_s: Optional[float] = None) -> dict[str, Any]:
    from .workers import tasks

    cfg = get_config()
    budget = budget_s if budget_s is not None else cfg.runner_budget_minutes * 60
    deadline = time.monotonic() + budget

    with SessionLocal() as db:
        run = JobRun(name="runner")
        db.add(run)
        db.commit()
        run_id = run.id

    summary: dict[str, Any] = {}
    status, detail = "ok", None
    try:
        for name, every, attr in PERIODIC:
            if time.monotonic() >= deadline or not _periodic_due(name, every):
                continue
            try:
                getattr(tasks, attr)()
                summary[name] = "ok"
            except Exception as e:  # tracked() already logged it; one failing job must not stop the cycle
                summary[name] = f"error: {e}"

        try:
            summary["plan"] = tasks.tick()
        except Exception as e:
            summary["plan"] = f"error: {e}"

        summary["pipeline"] = process_videos(deadline)

        try:  # first comments of published videos (YouTube needs a few minutes after the upload)
            from .services import first_comment

            with SessionLocal() as db:
                summary["comments"] = first_comment.process_pending(db)
        except Exception as e:
            summary["comments"] = f"error: {e}"
        try:  # friendly AI replies to viewers' comments (self-throttled, capped per day)
            from .services import comment_replies

            with SessionLocal() as db:
                summary["replies"] = comment_replies.process(db)
        except Exception as e:
            summary["replies"] = f"error: {e}"
    except BaseException as e:
        status, detail = "error", f"{type(e).__name__}: {e}"
        log_event("runner", f"cycle crashed: {detail}", level="ERROR")
        raise
    finally:
        with SessionLocal() as db:
            r = db.get(JobRun, run_id)
            if r:
                r.status = status
                r.detail = detail or json.dumps(summary, default=str)[:480]
                r.finished_at = utcnow()
                db.commit()
    log_event("runner", "cycle finished", summary=json.dumps(summary, default=str))
    return summary


def main() -> int:
    setup_logging()
    migrate()
    summary = run_cycle()
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
