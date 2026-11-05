from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..events import log_event
from ..models import Idea, Insight, TopicPerformance, utcnow
from ..security import require_admin
from ..services.text_utils import fingerprint
from .common import idea_dict, iso

router = APIRouter(prefix="/api", tags=["director"], dependencies=[Depends(require_admin)])


@router.get("/ideas")
def list_ideas(status: Optional[str] = None, limit: int = Query(60, ge=1, le=200), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db)):
    stmt = select(Idea).options(selectinload(Idea.video))
    if status:
        stmt = stmt.where(Idea.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(stmt.order_by(Idea.score.desc(), Idea.created_at.desc()).limit(limit).offset(offset)
                      ).scalars().all()
    return {"total": total, "items": [idea_dict(i) for i in rows]}


@router.post("/ideas")
def add_idea(body: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Owner adds their own topic; it is de-duplicated like AI ideas."""
    topic = str(body.get("topic", "")).strip()
    if len(topic) < 5:
        raise HTTPException(422, "topic is too short")
    fp = fingerprint(topic)
    if db.execute(select(Idea.id).where(Idea.fingerprint == fp)).first():
        raise HTTPException(409, "a very similar idea already exists")
    idea = Idea(topic=topic[:300], angle=str(body.get("angle", ""))[:1000], hook=str(body.get("hook", ""))[:500],
                keywords=body.get("keywords") or [], category=str(body.get("category", ""))[:80],
                source="manual", status="approved", score=100.0, fingerprint=fp)
    db.add(idea)
    db.commit()
    log_event("director", f"manual idea added: {idea.topic}")
    return idea_dict(idea)


@router.patch("/ideas/{idea_id}")
def update_idea(idea_id: int, body: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    idea = db.get(Idea, idea_id)
    if idea is None:
        raise HTTPException(404, "idea not found")
    st = body.get("status")
    if st not in ("new", "approved", "rejected"):
        raise HTTPException(422, "status must be new | approved | rejected")
    if idea.status == "used":
        raise HTTPException(400, "idea already used")
    idea.status = st
    db.commit()
    return idea_dict(idea)


@router.post("/director/run")
def run_director():
    from ..workers.tasks import generate_ideas_task

    generate_ideas_task.delay()
    log_event("api", "AI Director run requested by owner")
    return {"queued": True}


@router.get("/director/state")
def director_state(db: Session = Depends(get_db)):
    perf = db.execute(select(TopicPerformance).order_by(TopicPerformance.score.desc())).scalars().all()
    insight = db.execute(select(Insight).order_by(Insight.created_at.desc()).limit(1)).scalar_one_or_none()
    counts = dict(db.execute(select(Idea.status, func.count()).group_by(Idea.status)).all())
    return {
        "idea_counts": counts,
        "insight": {"created_at": iso(insight.created_at), "summary": insight.summary, "data": insight.data}
        if insight else None,
        "performance": [
            {"kind": p.kind, "key": p.key, "videos": p.videos_count, "avg_views": round(p.avg_views, 1),
             "avg_retention": round(p.avg_retention, 1), "avg_engagement": round(p.avg_engagement, 4),
             "score": round(p.score, 1)}
            for p in perf
        ],
    }
