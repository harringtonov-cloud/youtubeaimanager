"""Structured logging: stdout + persistent DB log (shown on the dashboard's System Logs page)."""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete

from .config import get_config

_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    level = getattr(logging, get_config().log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"yam.{name}")


def log_event(
    component: str,
    message: str,
    level: str = "INFO",
    video_id: Optional[int] = None,
    **data: Any,
) -> None:
    """Write to stdout and to the DB. Never raises — logging must not break the pipeline."""
    lg = get_logger(component)
    lvl = getattr(logging, level.upper(), logging.INFO)
    lg.log(lvl, "%s%s", message, f" {data}" if data else "")
    try:
        from .db import SessionLocal
        from .models import LogEntry

        with SessionLocal() as db:
            db.add(
                LogEntry(
                    level=level.upper(),
                    component=component,
                    message=message[:4000],
                    data=_jsonable(data) or None,
                    video_id=video_id,
                )
            )
            db.commit()
    except Exception:  # pragma: no cover - last-resort safety
        lg.exception("failed to persist log entry")


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, (list, dict)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


def prune_logs(days: int = 30) -> int:
    from .db import SessionLocal
    from .models import LogEntry

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with SessionLocal() as db:
        res = db.execute(delete(LogEntry).where(LogEntry.ts < cutoff))
        db.commit()
        return res.rowcount or 0
