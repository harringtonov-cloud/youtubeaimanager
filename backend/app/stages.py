"""Video pipeline state machine: stage locks, transitions, failure accounting.

All coordination lives in Postgres, so it survives worker restarts and works with several workers.
A stage "lock" is `processing_stage` + `locked_at`; it is taken with an atomic UPDATE (compare-and-set)
and automatically considered stale after a per-stage TTL, so a crashed worker never blocks a video.
"""
from __future__ import annotations

import traceback
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from .models import (
    FAILED,
    PLANNED,
    PUBLISHED,
    PUBLISHING,
    READY,
    RENDERED,
    SCRIPTED,
    SEO_READY,
    Video,
    utcnow,
)

STAGES = ["script", "seo", "render", "thumbnail", "publish"]

# stage -> statuses in which the stage may start
STAGE_EXPECTS: dict[str, tuple[str, ...]] = {
    "script": (PLANNED,),
    "seo": (SCRIPTED,),
    "render": (SEO_READY,),
    "thumbnail": (RENDERED,),
    "publish": (READY, PUBLISHING),
}
# stage -> status after success
STAGE_DONE: dict[str, str] = {
    "script": SCRIPTED,
    "seo": SEO_READY,
    "render": RENDERED,
    "thumbnail": READY,
    "publish": PUBLISHED,
}
# stage that follows automatically (publish is gated by the scheduler, not chained)
NEXT_STAGE: dict[str, Optional[str]] = {
    "script": "seo",
    "seo": "render",
    "render": "thumbnail",
    "thumbnail": None,
    "publish": None,
}
# minutes after which a lock is considered abandoned
LOCK_TTL_MIN: dict[str, int] = {"script": 10, "seo": 10, "render": 60, "thumbnail": 10, "publish": 60}


class PermanentError(Exception):
    """Do not retry (bad configuration, rejected content, revoked credentials...)."""


class Deferred(Exception):
    """The stage cannot run right now (e.g. auto-publish paused). Not a failure: state is untouched."""


def release(db: Session, video: Video) -> None:
    db.rollback()
    video = db.get(Video, video.id)
    video.processing_stage = None
    video.locked_at = None
    db.commit()


def status_to_stage(status: str) -> Optional[str]:
    for stage, expected in STAGE_EXPECTS.items():
        if status in expected:
            return stage
    return None


def acquire(db: Session, video_id: int, stage: str) -> Optional[Video]:
    """Atomically take the stage lock. Returns the Video, or None if it can't/shouldn't run."""
    now = utcnow()
    stale = now - timedelta(minutes=LOCK_TTL_MIN[stage])
    res = db.execute(
        update(Video)
        .where(
            Video.id == video_id,
            Video.status.in_(STAGE_EXPECTS[stage]),
            or_(Video.processing_stage.is_(None), Video.locked_at < stale),
        )
        .values(processing_stage=stage, locked_at=now)
    )
    db.commit()
    if res.rowcount != 1:
        return None
    video = db.get(Video, video_id)
    db.refresh(video)
    return video


def heartbeat(db: Session, video: Video) -> None:
    """Extend the lock during long stages (rendering, uploading)."""
    video.locked_at = utcnow()
    db.commit()


def complete(db: Session, video: Video, stage: str) -> None:
    video.status = STAGE_DONE[stage]
    video.processing_stage = None
    video.locked_at = None
    video.attempts = 0
    video.error = None
    video.failed_stage = None
    db.commit()


def register_failure(db: Session, video: Video, stage: str, exc: BaseException, max_attempts: int,
                     permanent: bool = False) -> str:
    """Record a failure. Returns 'retry' or 'failed'."""
    db.rollback()
    video = db.get(Video, video.id)
    video.attempts = (video.attempts or 0) + 1
    video.error = f"[{stage}] {type(exc).__name__}: {exc}"[:2000]
    video.processing_stage = None
    video.locked_at = None
    if permanent or video.attempts >= max_attempts:
        video.status = FAILED
        video.failed_stage = stage
        outcome = "failed"
    else:
        outcome = "retry"
    db.commit()
    return outcome


def backoff_seconds(attempt: int) -> int:
    return min(60 * (2 ** max(attempt - 1, 0)), 30 * 60)


def reset_for_retry(db: Session, video: Video) -> Optional[str]:
    """Dashboard 'Retry': put a failed video back to the status that re-runs its failed stage."""
    stage = video.failed_stage or "script"
    expected = STAGE_EXPECTS[stage][0]
    video.status = expected
    video.attempts = 0
    video.error = None
    video.processing_stage = None
    video.locked_at = None
    video.failed_stage = None
    db.commit()
    return stage


def is_locked(video: Video, now: Optional[datetime] = None) -> bool:
    if not video.processing_stage or not video.locked_at:
        return False
    now = now or utcnow()
    ttl = LOCK_TTL_MIN.get(video.processing_stage, 10)
    return video.locked_at > now - timedelta(minutes=ttl)


def format_exc(exc: BaseException) -> str:
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()
