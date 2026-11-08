from __future__ import annotations

import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

import redis

from .config import get_config

HEARTBEAT_KEY = "yam:worker:heartbeat"
QUEUE_NAME = "yam"

# how stale the worker signal may be before the dashboard calls it offline
ALIVE_LIMIT_CELERY_S = 120
ALIVE_LIMIT_INLINE_S = 3 * 3600  # scheduled runner: cron every 30 min, GitHub may delay runs


@lru_cache
def get_redis() -> "redis.Redis":
    return redis.Redis.from_url(get_config().redis_url, socket_connect_timeout=3, socket_timeout=3,
                                decode_responses=True)


def _inline() -> bool:
    return get_config().inline_mode


def worker_alive_limit() -> int:
    return ALIVE_LIMIT_INLINE_S if _inline() else ALIVE_LIMIT_CELERY_S


def beat() -> None:
    if _inline():
        return
    get_redis().set(HEARTBEAT_KEY, str(time.time()), ex=180)


def _runner_age_from_db() -> Optional[float]:
    """Inline mode: the scheduled runner records every cycle as a 'runner' job in Postgres."""
    try:
        from sqlalchemy import select

        from .db import SessionLocal
        from .models import JobRun

        with SessionLocal() as db:
            ts = db.execute(
                select(JobRun.started_at).where(JobRun.name == "runner").order_by(JobRun.started_at.desc()).limit(1)
            ).scalar_one_or_none()
    except Exception:
        return None
    if ts is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())


def worker_heartbeat_age() -> Optional[float]:
    """Seconds since the last worker signal, or None if the worker never reported / expired."""
    if _inline():
        return _runner_age_from_db()
    try:
        v = get_redis().get(HEARTBEAT_KEY)
    except Exception:
        return None
    if not v:
        return None
    return max(0.0, time.time() - float(v))


def queue_length() -> Optional[int]:
    if _inline():
        return None
    try:
        return int(get_redis().llen(QUEUE_NAME))
    except Exception:
        return None


def redis_ok() -> bool:
    if _inline():
        return True  # Redis is not used in inline mode
    try:
        return bool(get_redis().ping())
    except Exception:
        return False
