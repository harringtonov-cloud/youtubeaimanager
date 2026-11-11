from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..events import log_event
from ..models import (
    ACTIVE_STATUSES,
    CANCELLED,
    FAILED,
    PUBLISHED,
    PUBLISHING,
    READY,
    Idea,
    Publication,
    Video,
    utcnow,
)
from ..security import require_admin
from ..stages import reset_for_retry
from ..storage import get_storage
from .common import iso, publication_dict, snapshot_dict, video_dict

router = APIRouter(prefix="/api", tags=["videos"], dependencies=[Depends(require_admin)])


def _get_video(db: Session, video_id: int) -> Video:
    v = db.execute(
        select(Video).where(Video.id == video_id).options(selectinload(Video.publication), selectinload(Video.idea))
    ).scalar_one_or_none()
    if v is None:
        raise HTTPException(404, "video not found")
    return v


@router.get("/videos")
def list_videos(
    status: Optional[str] = Query(None, description="comma-separated statuses"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Video).options(selectinload(Video.publication), selectinload(Video.idea))
    if status:
        stmt = stmt.where(Video.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(stmt.order_by(Video.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    return {"total": total, "items": [video_dict(v) for v in rows]}


@router.get("/videos/{video_id}")
def get_video(video_id: int, db: Session = Depends(get_db)):
    v = _get_video(db, video_id)
    d = video_dict(v, full=True)
    if v.publication and v.publication.snapshots:
        latest = max(v.publication.snapshots, key=lambda s: s.fetched_at)
        d["analytics"] = snapshot_dict(latest)
    return d


@router.get("/videos/{video_id}/media")
def video_media(video_id: int, kind: str = Query("video", pattern="^(video|thumbnail|subtitles)$"),
                db: Session = Depends(get_db)):
    v = _get_video(db, video_id)
    key = {"video": v.video_key, "thumbnail": v.thumbnail_key, "subtitles": v.subtitle_key}[kind]
    if not key:
        raise HTTPException(404, f"no {kind} yet")
    return {"url": get_storage().url_for(key)}


@router.post("/videos/{video_id}/retry")
def retry_video(video_id: int, db: Session = Depends(get_db)):
    from ..workers.tasks import enqueue_stage

    v = _get_video(db, video_id)
    if v.status != FAILED:
        raise HTTPException(400, f"only failed videos can be retried (status is '{v.status}')")
    stage = reset_for_retry(db, v)
    enqueue_stage(v.id, stage)
    log_event("api", f"retry requested for stage '{stage}'", video_id=v.id)
    return {"ok": True, "stage": stage}


@router.post("/videos/{video_id}/cancel")
def cancel_video(video_id: int, db: Session = Depends(get_db)):
    v = _get_video(db, video_id)
    if v.status in (PUBLISHED, PUBLISHING):
        raise HTTPException(400, "cannot cancel a video that is being / has been published")
    v.status = CANCELLED
    v.processing_stage = None
    v.locked_at = None
    db.commit()
    log_event("api", "video cancelled by owner", video_id=v.id)
    return {"ok": True}


@router.delete("/videos/{video_id}")
def delete_video(video_id: int, db: Session = Depends(get_db)):
    """Remove a video that is not on YouTube (failed, ready, cancelled, ...): record, files and the pending upload.

    The idea stays in the database as 'rejected' so the same topic is not generated again.
    """
    v = _get_video(db, video_id)
    pub = v.publication
    if v.status in (PUBLISHED, PUBLISHING) or (pub is not None and pub.youtube_video_id):
        raise HTTPException(400, "this video is (being) published on YouTube: delete it in YouTube Studio instead")
    keys = {k for k in (v.video_key, v.subtitle_key, v.thumbnail_key, f"videos/{v.id}/frame.jpg") if k}
    title, idea = v.title, v.idea
    if pub is not None:
        db.delete(pub)
        db.flush()
    db.delete(v)
    if idea is not None:
        idea.status = "rejected"
    db.commit()
    storage = get_storage()
    for k in keys:
        try:
            storage.delete(k)
        except Exception:  # a leftover file is harmless; the record is already gone
            pass
    log_event("api", f"video #{video_id} deleted by owner ({title})")
    return {"ok": True}


@router.post("/videos/{video_id}/approve")
def approve_video(video_id: int, db: Session = Depends(get_db)):
    """Owner approval: the video becomes eligible for publishing at its scheduled time."""
    v = _get_video(db, video_id)
    if v.status in (PUBLISHED, CANCELLED, FAILED):
        raise HTTPException(400, f"cannot approve a video in status '{v.status}'")
    v.approved = True
    db.commit()
    log_event("api", "video approved for publishing", video_id=v.id)
    return {"ok": True}


@router.post("/videos/{video_id}/publish-now")
def publish_now(video_id: int, db: Session = Depends(get_db)):
    from ..services import youtube
    from ..workers.tasks import enqueue_stage

    v = _get_video(db, video_id)
    if v.status in (PUBLISHED, CANCELLED, FAILED, PUBLISHING):
        raise HTTPException(400, f"cannot publish a video in status '{v.status}'")
    if not youtube.is_connected(db):
        raise HTTPException(409, "YouTube is not connected")
    v.approved = True
    v.scheduled_at = utcnow()
    db.commit()
    if v.status == READY:
        enqueue_stage(v.id, "publish")
    log_event("api", "publish-now requested", video_id=v.id)
    return {"ok": True, "queued": v.status == READY}


@router.patch("/videos/{video_id}/schedule")
def reschedule(video_id: int, body: dict = Body(...), db: Session = Depends(get_db)):
    v = _get_video(db, video_id)
    if v.status in (PUBLISHED, PUBLISHING, CANCELLED):
        raise HTTPException(400, f"cannot reschedule a video in status '{v.status}'")
    raw = body.get("scheduled_at")
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(422, "scheduled_at must be an ISO-8601 datetime")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    v.scheduled_at = dt.astimezone(timezone.utc)
    db.commit()
    return {"ok": True, "scheduled_at": iso(v.scheduled_at)}


@router.post("/videos/from-idea/{idea_id}")
def video_from_idea(idea_id: int, db: Session = Depends(get_db)):
    """Create a video immediately from a chosen idea (next free slot) and start production."""
    from ..services import director
    from ..workers.tasks import enqueue_stage

    idea = db.get(Idea, idea_id)
    if idea is None:
        raise HTTPException(404, "idea not found")
    if idea.video is not None:
        raise HTTPException(409, "a video already exists for this idea")
    if idea.status == "rejected":
        raise HTTPException(400, "idea was rejected")
    v = director.create_video_for_idea(db, idea)
    enqueue_stage(v.id, "script")
    return video_dict(_get_video(db, v.id))


@router.get("/calendar")
def calendar(
    start: Optional[str] = None,
    end: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Scheduled videos + open (still empty) publishing slots in a date range."""
    from datetime import timedelta

    from ..scheduling import slots_between
    from ..settings_store import get_settings

    now = utcnow()
    try:
        d0 = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else now - timedelta(days=7)
        d1 = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else now + timedelta(days=21)
    except Exception:
        raise HTTPException(422, "start/end must be ISO-8601")
    if d0.tzinfo is None:
        d0 = d0.replace(tzinfo=timezone.utc)
    if d1.tzinfo is None:
        d1 = d1.replace(tzinfo=timezone.utc)

    s = get_settings(db)
    rows = db.execute(
        select(Video)
        .where(Video.scheduled_at >= d0, Video.scheduled_at < d1, Video.status != CANCELLED)
        .options(selectinload(Video.publication), selectinload(Video.idea))
        .order_by(Video.scheduled_at)
    ).scalars().all()
    taken = {v.scheduled_at for v in rows if v.scheduled_at}
    open_slots = [
        iso(t) for t in slots_between(max(d0, now), d1, s.publish_times, s.timezone) if t not in taken
    ]
    return {
        "timezone": s.timezone,
        "publish_times": s.publish_times,
        "items": [video_dict(v) for v in rows],
        "open_slots": open_slots,
    }


@router.get("/published")
def published(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = (
        select(Publication)
        .options(selectinload(Publication.video).selectinload(Video.idea), selectinload(Publication.snapshots))
        .order_by(Publication.created_at.desc())
    )
    total = db.scalar(select(func.count()).select_from(Publication)) or 0
    rows = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
    items = []
    for p in rows:
        latest = max(p.snapshots, key=lambda s: s.fetched_at) if p.snapshots else None
        d = publication_dict(p) or {}
        d.update(
            {
                "video_id": p.video_id,
                "title": p.title or p.video.title,
                "topic": p.video.idea.topic if p.video.idea else None,
                "has_thumbnail": bool(p.video.thumbnail_key),
                "analytics": snapshot_dict(latest),
            }
        )
        items.append(d)
    return {"total": total, "items": items}
