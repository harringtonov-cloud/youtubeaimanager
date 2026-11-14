"""Celery tasks: pipeline stages + scheduled jobs.

Every task is idempotent: state lives in Postgres and stage locks (see app/stages.py) make
duplicate or re-delivered messages harmless.
"""
from __future__ import annotations

import functools
from datetime import timedelta
from typing import Any, Callable, Optional

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, select

from ..config import get_config
from ..db import SessionLocal
from ..events import log_event, prune_logs
from ..models import (
    PLANNED,
    PUBLISHING,
    RENDERED,
    SCRIPTED,
    SEO_READY,
    JobRun,
    OAuthState,
    Video,
    utcnow,
)
from ..redis_util import beat
from ..settings_store import get_settings
from ..stages import (
    NEXT_STAGE,
    Deferred,
    PermanentError,
    acquire,
    backoff_seconds,
    complete,
    is_locked,
    register_failure,
    release,
    status_to_stage,
)
from .celery_app import celery_app


# --------------------------------------------------------------------------- helpers
def tracked(name: str) -> Callable:
    """Record scheduled-job runs in the DB (shown on the dashboard) and log failures."""

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any):
            with SessionLocal() as db:
                run = JobRun(name=name)
                db.add(run)
                db.commit()
                run_id = run.id
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                _finish_run(run_id, "error", f"{type(e).__name__}: {e}")
                log_event("worker", f"job '{name}' failed: {e}", level="ERROR")
                raise
            _finish_run(run_id, "ok", None if result is None else str(result)[:500])
            return result

        return wrapper

    return deco


def _finish_run(run_id: int, status: str, detail: Optional[str]) -> None:
    try:
        with SessionLocal() as db:
            run = db.get(JobRun, run_id)
            if run:
                run.status = status
                run.detail = detail
                run.finished_at = utcnow()
                db.commit()
    except Exception:  # pragma: no cover
        pass


def enqueue_stage(video_id: int, stage: str, countdown: int = 0) -> None:
    if get_config().inline_mode:
        return  # no broker: the scheduled runner (app.runner) picks videos up by their status
    run_stage.apply_async(args=[video_id, stage], countdown=countdown)


def _runners() -> dict[str, Callable]:
    # imported lazily so a missing optional dependency only breaks the stage that needs it
    from ..services import publisher, scriptwriter, seo, thumbnail, video_creator

    return {
        "script": scriptwriter.write_script,
        "seo": seo.generate_seo,
        "render": video_creator.render_video,
        "thumbnail": thumbnail.make_thumbnail,
        "publish": publisher.publish_video,
    }


# --------------------------------------------------------------------------- pipeline stage
@celery_app.task(
    bind=True,
    name="yam.run_stage",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=None,
    soft_time_limit=55 * 60,
    time_limit=58 * 60,
)
def run_stage(self, video_id: int, stage: str):
    with SessionLocal() as db:
        max_attempts = get_settings(db).max_attempts
        video = acquire(db, video_id, stage)
        if video is None:
            return "skipped"  # not in the right status, or another worker holds the lock
        log_event("pipeline", f"stage '{stage}' started", video_id=video_id)
        try:
            _runners()[stage](db, video)
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
        except SoftTimeLimitExceeded as e:
            outcome = register_failure(db, video, stage, e, max_attempts)
            log_event("pipeline", f"stage '{stage}' timed out", level="ERROR", video_id=video_id)
            if outcome == "retry":
                raise self.retry(countdown=60, exc=e)
            return "failed"
        except Exception as e:  # transient or unexpected -> backoff retry, then fail
            outcome = register_failure(db, video, stage, e, max_attempts)
            attempts = db.get(Video, video_id).attempts
            log_event(
                "pipeline",
                f"stage '{stage}' error (attempt {attempts}/{max_attempts}): {e}",
                level="ERROR" if outcome == "failed" else "WARNING",
                video_id=video_id,
            )
            if outcome == "retry":
                raise self.retry(countdown=backoff_seconds(attempts), exc=e)
            return "failed"

    log_event("pipeline", f"stage '{stage}' done", video_id=video_id)
    nxt = NEXT_STAGE.get(stage)
    if nxt:
        enqueue_stage(video_id, nxt)
    return "ok"


# --------------------------------------------------------------------------- scheduled jobs
@celery_app.task(name="yam.heartbeat", ignore_result=True)
def heartbeat():
    beat()


def _redispatch_stalled(db) -> int:
    """Resume videos whose message was lost or whose worker died (restart safety)."""
    now = utcnow()
    idle = now - timedelta(minutes=15)
    rows = (
        db.execute(
            select(Video).where(
                Video.status.in_([PLANNED, SCRIPTED, SEO_READY, RENDERED, PUBLISHING]),
                Video.updated_at < idle,
            )
        )
        .scalars()
        .all()
    )
    n = 0
    for v in rows:
        if is_locked(v, now):
            continue
        stage = status_to_stage(v.status)
        if stage:
            enqueue_stage(v.id, stage)
            n += 1
    return n


@celery_app.task(name="yam.tick")
@tracked("tick")
def tick():
    """Every 5 minutes: resume stalled work, keep the calendar full, dispatch due publications."""
    from ..services import director, first_comment, publisher

    with SessionLocal() as db:
        resumed = _redispatch_stalled(db)
        new_ids = director.plan_content(db)
        due = publisher.due_videos(db)
    for vid in new_ids:
        enqueue_stage(vid, "script")
    for vid in due:
        enqueue_stage(vid, "publish")
    try:  # first comments are posted a few minutes after the upload, with retries
        with SessionLocal() as db:
            comments = first_comment.process_pending(db)
    except Exception as e:
        comments = f"error: {e}"
    return f"resumed={resumed} planned={len(new_ids)} publish_due={len(due)} comments={comments}"


@celery_app.task(name="yam.generate_ideas")
@tracked("generate_ideas")
def generate_ideas_task():
    from ..services import director

    with SessionLocal() as db:
        created = director.generate_ideas(db)
        new_ids = director.plan_content(db)
    for vid in new_ids:
        enqueue_stage(vid, "script")
    return f"ideas={created} planned={len(new_ids)}"


@celery_app.task(name="yam.sync_analytics")
@tracked("sync_analytics")
def sync_analytics_task():
    from ..services import analytics

    with SessionLocal() as db:
        return analytics.sync_analytics(db)


@celery_app.task(name="yam.learn")
@tracked("learn")
def learn_task():
    from ..services import analytics

    with SessionLocal() as db:
        analytics.sync_analytics(db)
        return analytics.learn(db)


@celery_app.task(name="yam.verify_youtube")
@tracked("verify_youtube")
def verify_youtube_task():
    from ..services import youtube

    with SessionLocal() as db:
        return youtube.verify_connection(db)


@celery_app.task(name="yam.housekeeping")
@tracked("housekeeping")
def housekeeping_task():
    removed_logs = prune_logs(30)
    with SessionLocal() as db:
        db.execute(delete(JobRun).where(JobRun.started_at < utcnow() - timedelta(days=7)))
        db.execute(delete(OAuthState).where(OAuthState.created_at < utcnow() - timedelta(days=1)))
        db.commit()
    return f"logs_removed={removed_logs}"
