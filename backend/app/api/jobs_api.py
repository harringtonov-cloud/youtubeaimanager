from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..events import log_event
from ..security import require_admin

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_admin)])

_ALLOWED = {"tick", "generate_ideas", "sync_analytics", "learn", "verify_youtube", "housekeeping"}


@router.post("/run/{name}")
def run_job(name: str):
    """Trigger a scheduled job immediately (dashboard 'Run now' buttons)."""
    if name not in _ALLOWED:
        raise HTTPException(404, f"unknown job '{name}'")
    from ..workers import tasks

    task = {
        "tick": tasks.tick, "generate_ideas": tasks.generate_ideas_task, "sync_analytics": tasks.sync_analytics_task,
        "learn": tasks.learn_task, "verify_youtube": tasks.verify_youtube_task,
        "housekeeping": tasks.housekeeping_task,
    }[name]
    task.delay()
    log_event("api", f"job '{name}' triggered manually")
    return {"queued": True, "job": name}
