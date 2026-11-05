from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import JobRun, LogEntry
from ..security import require_admin
from .common import iso, log_dict

router = APIRouter(prefix="/api/logs", tags=["logs"], dependencies=[Depends(require_admin)])


@router.get("")
def list_logs(
    level: Optional[str] = Query(None, description="minimum level: DEBUG|INFO|WARNING|ERROR"),
    component: Optional[str] = None,
    video_id: Optional[int] = None,
    q: Optional[str] = Query(None, description="substring in message"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
    stmt = select(LogEntry)
    if level and level.upper() in order:
        allowed = [k for k, v in order.items() if v >= order[level.upper()]]
        stmt = stmt.where(LogEntry.level.in_(allowed))
    if component:
        stmt = stmt.where(LogEntry.component == component)
    if video_id is not None:
        stmt = stmt.where(LogEntry.video_id == video_id)
    if q:
        stmt = stmt.where(LogEntry.message.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(LogEntry.ts.desc()).limit(limit).offset(offset)).scalars().all()
    components = [c for (c,) in db.execute(select(LogEntry.component).distinct()).all()]
    return {"total": total, "items": [log_dict(r) for r in rows], "components": sorted(components)}


@router.get("/jobs")
def list_jobs(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    rows = db.execute(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)).scalars().all()
    return {
        "items": [
            {
                "id": j.id,
                "name": j.name,
                "status": j.status,
                "started_at": iso(j.started_at),
                "finished_at": iso(j.finished_at),
                "detail": j.detail,
            }
            for j in rows
        ]
    }
