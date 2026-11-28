"""Which YouTube channel ("account") the current request / worker pass works on.

One installation can manage several YouTube channels of the same owner. Every piece of channel data (ideas, videos,
settings, learning memory ...) belongs to exactly one account. Instead of touching every query in the code base the
current account lives in a ContextVar and `db.py` adds the filter to every ORM statement automatically.

* `current() is None`  -> no filtering (tests, one-off scripts, a database that has no channel yet).
* The oldest account is the "legacy" one: it owns everything created before multi-channel support existed, so its
  settings keys, idea fingerprints and NULL-owner rows stay exactly as they were.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class Scope:
    account_id: int
    legacy: bool = False  # True for the oldest (original) channel


_scope: ContextVar[Optional[Scope]] = ContextVar("channel_scope", default=None)


def current() -> Optional[Scope]:
    return _scope.get()


def set_scope(sc: Optional[Scope]) -> None:
    _scope.set(sc)


@contextmanager
def use(sc: Optional[Scope]) -> Iterator[None]:
    token = _scope.set(sc)
    try:
        yield
    finally:
        _scope.reset(token)


def scoped_key(key: str) -> str:
    """Settings / flag key for the current channel. The legacy channel keeps the original, unprefixed keys."""
    sc = _scope.get()
    if sc is None or sc.legacy:
        return key
    return f"a{sc.account_id}:{key}"


def salt_fingerprint(fp: str) -> str:
    """Idea fingerprints are globally unique in the database; make them unique per channel (same 64-hex shape)."""
    sc = _scope.get()
    if sc is None or sc.legacy:
        return fp
    return hashlib.sha256(f"{sc.account_id}|{fp}".encode()).hexdigest()


def resolve(db, requested: object = None) -> Optional[Scope]:
    """Pick the scope for a request: the requested account id if it exists, otherwise the oldest one.

    `db` must be a session used while no scope is active (the account list is global)."""
    from sqlalchemy import select

    from .models import YouTubeAccount

    ids = [i for (i,) in db.execute(select(YouTubeAccount.id).order_by(YouTubeAccount.id)).all()]
    if not ids:
        return None
    legacy_id = ids[0]
    chosen = legacy_id
    try:
        want = int(str(requested).strip()) if requested not in (None, "") else None
    except ValueError:
        want = None
    if want in ids:
        chosen = want
    return Scope(account_id=chosen, legacy=(chosen == legacy_id))


def all_scopes(db) -> list[Scope]:
    """One Scope per connected channel, oldest first (the worker walks through them)."""
    from sqlalchemy import select

    from .models import YouTubeAccount

    ids = [i for (i,) in db.execute(select(YouTubeAccount.id).order_by(YouTubeAccount.id)).all()]
    return [Scope(account_id=i, legacy=(n == 0)) for n, i in enumerate(ids)]
