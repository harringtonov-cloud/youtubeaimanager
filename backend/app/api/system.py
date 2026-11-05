from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..config import get_config
from ..db import get_db
from ..models import (
    ACTIVE_STATUSES,
    FAILED,
    PUBLISHED,
    Idea,
    JobRun,
    LogEntry,
    Publication,
    Video,
    YouTubeAccount,
    utcnow,
)
from ..redis_util import queue_length, redis_ok, worker_heartbeat_age
from ..security import require_admin
from ..settings_store import get_settings
from ..storage import get_storage
from .common import iso, log_dict

router = APIRouter(prefix="/api", tags=["system"])

VERSION = "1.0.0"


@router.get("/health")
def health(db: Session = Depends(get_db)):
    """Public liveness/readiness probe (no secrets)."""
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    r_ok = redis_ok()
    return {"status": "ok" if db_ok and r_ok else "degraded", "database": db_ok, "redis": r_ok, "version": VERSION}


@router.get("/auth/check", dependencies=[Depends(require_admin)])
def auth_check():
    return {"ok": True}


def _last_job(db: Session, name: str):
    return db.execute(
        select(JobRun).where(JobRun.name == name).order_by(JobRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()


def _job_dict(j):
    if j is None:
        return None
    return {
        "name": j.name,
        "status": j.status,
        "started_at": iso(j.started_at),
        "finished_at": iso(j.finished_at),
        "detail": j.detail,
    }


@router.get("/overview", dependencies=[Depends(require_admin)])
def overview(db: Session = Depends(get_db)):
    cfg = get_config()
    s = get_settings(db)
    now = utcnow()

    # --- server
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    hb_age = worker_heartbeat_age()
    worker_online = hb_age is not None and hb_age < 120
    try:
        storage_kind = get_storage().kind
    except Exception as e:  # misconfigured S3
        storage_kind = f"error: {e}"

    # --- youtube
    acct = db.execute(select(YouTubeAccount).order_by(YouTubeAccount.id.desc()).limit(1)).scalar_one_or_none()
    youtube = {
        "connected": bool(acct and acct.status == "connected"),
        "status": acct.status if acct else "not_connected",
        "channel_title": acct.channel_title if acct else None,
        "channel_id": acct.channel_id if acct else None,
        "subscriber_count": acct.subscriber_count if acct else None,
        "last_verified_at": iso(acct.last_verified_at) if acct else None,
        "last_error": acct.last_error if acct else None,
    }

    # --- queue / counts
    by_status = dict(db.execute(select(Video.status, func.count()).group_by(Video.status)).all())
    processing = db.scalar(select(func.count()).select_from(Video).where(Video.processing_stage.is_not(None))) or 0
    total_videos = sum(by_status.values())
    published_total = db.scalar(select(func.count()).select_from(Publication)
                                .where(Publication.status.in_(["published", "scheduled"]))) or 0
    ideas_by_status = dict(db.execute(select(Idea.status, func.count()).group_by(Idea.status)).all())
    next_scheduled = db.execute(
        select(Video.scheduled_at).where(Video.status.in_(ACTIVE_STATUSES), Video.scheduled_at > now)
        .order_by(Video.scheduled_at).limit(1)
    ).scalar_one_or_none()

    # --- activity
    errors = db.execute(
        select(LogEntry).where(LogEntry.level.in_(["ERROR", "CRITICAL"]), LogEntry.ts > now - timedelta(days=3))
        .order_by(LogEntry.ts.desc()).limit(8)
    ).scalars().all()
    actions = db.execute(select(LogEntry).where(LogEntry.level != "DEBUG").order_by(LogEntry.ts.desc()).limit(15)
                         ).scalars().all()

    # --- AI mode
    if not cfg.llm_api_key:
        ai_mode, ai_label = "unconfigured", "LLM key missing"
    elif not s.auto_publish_enabled:
        ai_mode, ai_label = "manual", "Producing videos, auto-publish OFF"
    elif youtube["status"] != "connected":
        ai_mode, ai_label = "paused", "Auto-publish paused: YouTube needs attention"
    else:
        ai_mode, ai_label = "autonomous", "Fully autonomous"

    return {
        "ai": {
            "mode": ai_mode,
            "label": ai_label,
            "auto_publish_enabled": s.auto_publish_enabled,
            "llm_configured": bool(cfg.llm_api_key),
            "model": cfg.llm_model,
            "last_tick": _job_dict(_last_job(db, "tick")),
            "last_ideas_run": _job_dict(_last_job(db, "generate_ideas")),
            "last_analytics_sync": _job_dict(_last_job(db, "sync_analytics")),
            "next_publication_at": iso(next_scheduled),
        },
        "server": {
            "database": db_ok,
            "redis": redis_ok(),
            "worker_online": worker_online,
            "worker_heartbeat_age_s": round(hb_age, 1) if hb_age is not None else None,
            "storage": storage_kind,
            "version": VERSION,
        },
        "youtube": youtube,
        "queue": {
            "broker_length": queue_length(),
            "processing": processing,
            "waiting": sum(by_status.get(k, 0) for k in ("planned", "scripted", "seo_ready", "rendered")),
            "ready": by_status.get("ready", 0),
            "failed": by_status.get(FAILED, 0),
            "by_status": by_status,
        },
        "counts": {
            "videos_created": total_videos - by_status.get("cancelled", 0),
            "videos_rendered": sum(by_status.get(k, 0) for k in ("rendered", "ready", "publishing", PUBLISHED)),
            "videos_published": published_total,
            "ideas_total": sum(ideas_by_status.values()),
            "ideas_new": ideas_by_status.get("new", 0) + ideas_by_status.get("approved", 0),
        },
        "recent_errors": [log_dict(e) for e in errors],
        "recent_actions": [log_dict(a) for a in actions],
    }
