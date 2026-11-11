"""Reliable trigger for scheduled publishing in the free setup.

GitHub's scheduled workflows are often delayed by hours, which misses publication slots. An external pinger
(UptimeRobot / cron-job.org, free) calls this URL every few minutes: it wakes the sleeping API and uploads
whatever is due. Production (ideas, scripts, FFmpeg) stays on the GitHub Runner.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from ..config import get_config
from ..db import SessionLocal
from ..events import log_event

router = APIRouter(prefix="/api/cron", tags=["cron"])


def _publish_due() -> None:
    from ..runner import execute_stage
    from ..services import publisher

    try:
        for _ in range(3):  # at most 3 uploads per ping
            with SessionLocal() as db:
                due = publisher.due_videos(db)
            if not due:
                break
            outcome = execute_stage(due[0], "publish")
            if outcome != "ok":
                break
    except Exception as e:  # never crash the API process
        log_event("api", f"cron publish crashed: {e}", level="ERROR")


@router.api_route("/tick", methods=["GET", "HEAD"])
def tick(background: BackgroundTasks, key: str = Query("")):
    expected = get_config().cron_key
    if not expected or not secrets.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(404, "not found")  # do not reveal that the endpoint exists
    with SessionLocal() as db:
        from ..services import publisher

        due = publisher.due_videos(db)
    if due:
        background.add_task(_publish_due)
    return {"ok": True, "due": len(due)}
