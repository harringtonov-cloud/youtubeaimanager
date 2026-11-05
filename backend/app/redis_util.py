from __future__ import annotations

import time
from functools import lru_cache
from typing import Optional

import redis

from .config import get_config

HEARTBEAT_KEY = "yam:worker:heartbeat"
QUEUE_NAME = "yam"


@lru_cache
def get_redis() -> "redis.Redis":
    return redis.Redis.from_url(get_config().redis_url, socket_connect_timeout=3, socket_timeout=3,
                                decode_responses=True)


def beat() -> None:
    get_redis().set(HEARTBEAT_KEY, str(time.time()), ex=180)


def worker_heartbeat_age() -> Optional[float]:
    """Seconds since the last worker heartbeat, or None if the worker never reported / expired."""
    try:
        v = get_redis().get(HEARTBEAT_KEY)
    except Exception:
        return None
    if not v:
        return None
    return max(0.0, time.time() - float(v))


def queue_length() -> Optional[int]:
    try:
        return int(get_redis().llen(QUEUE_NAME))
    except Exception:
        return None


def redis_ok() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False
