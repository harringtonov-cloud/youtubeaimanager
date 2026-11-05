from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from ..config import get_config
from ..redis_util import QUEUE_NAME

cfg = get_config()

celery_app = Celery("yam", broker=cfg.redis_url, include=["app.workers.tasks"])

celery_app.conf.update(
    task_default_queue=QUEUE_NAME,
    task_ignore_result=True,          # state lives in Postgres, not in the result backend
    task_acks_late=True,              # re-deliver if a worker dies mid-task
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    # must exceed the longest task, otherwise Redis re-delivers a still-running task
    broker_transport_options={"visibility_timeout": 3 * 3600},
    timezone="UTC",
    enable_utc=True,
    worker_hijack_root_logger=False,
    beat_schedule={
        # liveness signal for the dashboard
        "heartbeat": {"task": "yam.heartbeat", "schedule": 30.0},
        # watchdog + plan next slots + dispatch due publications
        "tick": {"task": "yam.tick", "schedule": 300.0},
        # idea research
        "generate-ideas": {"task": "yam.generate_ideas", "schedule": 6 * 3600.0},
        # analytics collection + learning
        "sync-analytics": {"task": "yam.sync_analytics", "schedule": 3 * 3600.0},
        "learn": {"task": "yam.learn", "schedule": crontab(hour=4, minute=15)},
        # YouTube connection health check
        "verify-youtube": {"task": "yam.verify_youtube", "schedule": 6 * 3600.0},
        # housekeeping
        "housekeeping": {"task": "yam.housekeeping", "schedule": crontab(hour=3, minute=30)},
    },
)
