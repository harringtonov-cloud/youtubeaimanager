from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, or_
from sqlalchemy.orm import Session, sessionmaker, with_loader_criteria

from . import scope

from .config import get_config

_cfg = get_config()

_connect_args: dict = {}
if _cfg.is_sqlite:
    # allow use across threads (FastAPI threadpool / tests)
    _connect_args = {"check_same_thread": False}
    path = _cfg.database_url.replace("sqlite:///", "", 1)
    if path and path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

engine = create_engine(
    _cfg.database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args=_connect_args,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for workers/services."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# --- per-channel data isolation (see scope.py) ---------------------------------------------------------------------
@event.listens_for(Session, "do_orm_execute")
def _filter_by_channel(state) -> None:
    """Every ORM select / update / delete only sees the rows of the current channel."""
    sc = scope.current()
    if sc is None or state.is_column_load or state.is_relationship_load:
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    from .models import SCOPED_MODELS, YouTubeAccount

    stmt = state.statement
    for cls in SCOPED_MODELS:
        crit = cls.account_id == sc.account_id
        if sc.legacy:  # rows created before multi-channel support have no owner: they belong to the original channel
            crit = or_(crit, cls.account_id.is_(None))
        stmt = stmt.options(with_loader_criteria(cls, crit, include_aliases=True))
    stmt = stmt.options(with_loader_criteria(YouTubeAccount, YouTubeAccount.id == sc.account_id, include_aliases=True))
    state.statement = stmt


@event.listens_for(Session, "before_flush")
def _stamp_channel(session, flush_context, instances) -> None:
    """New rows are owned by the current channel."""
    sc = scope.current()
    if sc is None:
        return
    from .models import SCOPED_MODELS

    for obj in session.new:
        if isinstance(obj, SCOPED_MODELS) and obj.account_id is None:
            obj.account_id = sc.account_id
