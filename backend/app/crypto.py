"""Symmetric encryption for secrets stored in the database (YouTube refresh token)."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import get_config


def _fernet() -> Fernet:
    secret = get_config().secret_key
    if not secret:
        raise RuntimeError("SECRET_KEY is not configured")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:  # SECRET_KEY was changed
        raise RuntimeError("Cannot decrypt stored secret: SECRET_KEY changed. Reconnect YouTube.") from e
