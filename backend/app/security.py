from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from .config import get_config


def require_admin(authorization: str | None = Header(default=None)) -> None:
    """Bearer-token guard for every dashboard API route. Fails closed if no token configured."""
    expected = get_config().admin_token
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ADMIN_TOKEN is not configured on the server")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    provided = authorization[7:].strip()
    if not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
