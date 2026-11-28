from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Header, HTTPException, status
from starlette.concurrency import run_in_threadpool

from . import scope
from .config import get_config


def _resolve_scope(requested: Optional[str]) -> Optional[scope.Scope]:
    from .db import SessionLocal

    with SessionLocal() as db:  # no scope is active here, so the whole channel list is visible
        return scope.resolve(db, requested)


async def require_admin(authorization: str | None = Header(default=None),
                        x_channel: str | None = Header(default=None)) -> None:
    """Bearer-token guard for every dashboard API route. Fails closed if no token configured.

    Also picks the YouTube channel the request works on (`X-Channel: <account id>`, default: the original channel).
    This is an async dependency on purpose: the ContextVar must be set in the request's own task so that the
    threadpool that runs the (sync) endpoint inherits it."""
    expected = get_config().admin_token
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ADMIN_TOKEN is not configured on the server")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    provided = authorization[7:].strip()
    if not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    scope.set_scope(await run_in_threadpool(_resolve_scope, x_channel))
