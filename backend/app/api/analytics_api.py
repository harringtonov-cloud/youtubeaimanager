from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import AnalyticsSnapshot, Insight, Publication, Video, utcnow
from ..security import require_admin
from .common import iso, snapshot_dict

router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(require_admin)])


def _latest(p: Publication):
    return max(p.snapshots, key=lambda s: s.fetched_at) if p.snapshots else None


@router.get("/summary")
def summary(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    pubs = db.execute(
        select(Publication).where(Publication.status.in_(["published", "scheduled"]))
        .options(selectinload(Publication.snapshots), selectinload(Publication.video).selectinload(Video.idea))
    ).scalars().all()
    since = utcnow() - timedelta(days=days)
    rows, tot = [], {"views": 0, "likes": 0, "comments": 0, "impressions": 0, "subs": 0, "minutes": 0.0}
    for p in pubs:
        s = _latest(p)
        if s is None:
            continue
        in_range = bool(p.published_at and p.published_at >= since)
        if in_range:
            tot["views"] += s.views
            tot["likes"] += s.likes
            tot["comments"] += s.comments
            tot["impressions"] += s.impressions or 0
            tot["subs"] += s.subs_gained or 0
            tot["minutes"] += s.estimated_minutes or 0
        eng = (s.likes + s.comments) / s.views if s.views else 0.0
        rows.append({
            "publication_id": p.id, "video_id": p.video_id, "title": p.title, "url": p.url,
            "topic": p.video.idea.topic if p.video.idea else None,
            "category": (p.video.idea.category if p.video.idea else "") or "",
            "published_at": iso(p.published_at), "engagement": round(eng, 4), **(snapshot_dict(s) or {}),
        })
    rows.sort(key=lambda r: r["views"], reverse=True)
    return {
        "days": days, "videos_with_data": len(rows),
        "totals": {**tot, "minutes": round(tot["minutes"], 1)},
        "avg_views": round(sum(r["views"] for r in rows) / len(rows), 1) if rows else 0,
        "top": rows[:5], "worst": rows[-5:][::-1] if len(rows) > 5 else [],
        "videos": rows,
    }


@router.get("/timeseries")
def timeseries(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    """Total views per day (sum of latest-known views of videos published that day)."""
    since = utcnow() - timedelta(days=days)
    pubs = db.execute(
        select(Publication).where(Publication.published_at >= since).options(selectinload(Publication.snapshots))
    ).scalars().all()
    by_day: dict[str, dict] = {}
    for p in pubs:
        s = _latest(p)
        day = p.published_at.date().isoformat()
        d = by_day.setdefault(day, {"day": day, "videos": 0, "views": 0})
        d["videos"] += 1
        d["views"] += s.views if s else 0
    return {"items": [by_day[k] for k in sorted(by_day)]}


@router.get("/video/{video_id}")
def video_history(video_id: int, db: Session = Depends(get_db)):
    p = db.execute(select(Publication).where(Publication.video_id == video_id)
                   .options(selectinload(Publication.snapshots))).scalar_one_or_none()
    if p is None:
        return {"items": []}
    return {"items": [snapshot_dict(s) for s in sorted(p.snapshots, key=lambda s: s.fetched_at)]}


@router.get("/insights")
def insights(limit: int = Query(10, ge=1, le=50), db: Session = Depends(get_db)):
    rows = db.execute(select(Insight).order_by(Insight.created_at.desc()).limit(limit)).scalars().all()
    return {"items": [{"id": i.id, "created_at": iso(i.created_at), "summary": i.summary, "data": i.data}
                      for i in rows]}


@router.post("/sync")
def sync_now(background: BackgroundTasks):
    from ..workers.tasks import sync_analytics_task
    from .jobs_api import dispatch

    return dispatch(sync_analytics_task, "sync_analytics", background)


@router.post("/learn")
def learn_now(background: BackgroundTasks):
    from ..workers.tasks import learn_task
    from .jobs_api import dispatch

    return dispatch(learn_task, "learn", background)
