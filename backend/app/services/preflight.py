"""Readiness checks. Auto-publish can only be switched on when every *blocking* check passes."""
from __future__ import annotations

import shutil
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_config
from ..redis_util import redis_ok, worker_heartbeat_age
from ..settings_store import get_settings
from ..storage import get_storage
from . import llm, youtube


def _on_ephemeral_cloud() -> bool:
    import os

    return any(os.environ.get(k) for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID"))


def _check(cid: str, label: str, ok: bool, detail: str, blocking: bool = True) -> dict[str, Any]:
    return {"id": cid, "label": label, "ok": bool(ok), "detail": detail, "blocking": blocking}


def run_checks(db: Session, live: bool = True) -> dict[str, Any]:
    c = get_config()
    s = get_settings(db)
    checks: list[dict[str, Any]] = []

    checks.append(_check("secrets", "ADMIN_TOKEN and SECRET_KEY set", bool(c.admin_token and c.secret_key),
                         "ok" if c.admin_token and c.secret_key else "set ADMIN_TOKEN and SECRET_KEY"))

    try:
        db.execute(text("SELECT 1"))
        checks.append(_check("database", "Database reachable", True, "ok"))
    except Exception as e:
        checks.append(_check("database", "Database reachable", False, str(e)[:200]))

    r_ok = redis_ok()
    checks.append(_check("redis", "Redis reachable", r_ok, "ok" if r_ok else "REDIS_URL unreachable"))
    age = worker_heartbeat_age()
    w_ok = age is not None and age < 120
    checks.append(_check("worker", "Worker running", w_ok,
                         f"heartbeat {age:.0f}s ago" if age is not None else "no heartbeat — start the worker service"))

    checks.append(_check("ffmpeg", "FFmpeg installed", bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
                         "ok" if shutil.which("ffmpeg") else "ffmpeg/ffprobe not found in PATH"))

    try:
        st = get_storage()
        if st.kind == "local" and _on_ephemeral_cloud():
            # Railway & co: the API and the worker are separate containers with separate, ephemeral disks.
            # Rendered videos would vanish on restart and the dashboard could not preview them.
            checks.append(_check("storage", "Persistent shared storage", False,
                                 "STORAGE_BACKEND=local on a cloud host: set STORAGE_BACKEND=s3 "
                                 "(Cloudflare R2 / Backblaze B2 / Railway Bucket / AWS S3)"))
        else:
            checks.append(_check("storage", f"Storage ({st.kind})", True, "ok"))
    except Exception as e:
        checks.append(_check("storage", "Storage", False, str(e)[:200]))

    if not llm.is_configured():
        checks.append(_check("llm", "LLM configured", False, "LLM_API_KEY is not set"))
    elif live:
        ok, detail = llm.ping()
        checks.append(_check("llm", "LLM responds", ok, detail))
    else:
        checks.append(_check("llm", "LLM configured", True, f"model '{c.llm_model}'"))

    tts = s.tts_provider or c.tts_provider
    if tts == "openai":
        ok = bool((c.tts_api_key or c.llm_api_key))
        checks.append(_check("tts", "Voice-over (OpenAI-compatible TTS)", ok, "ok" if ok else "TTS_API_KEY missing"))
    else:
        checks.append(_check("tts", "Voice-over (Edge TTS)", True, "no key required"))

    has_stock = bool(c.pexels_api_key or c.pixabay_api_key)
    checks.append(_check("stock", "Licensed stock footage", has_stock or s.visual_source == "generated",
                         "ok" if has_stock else "no Pexels/Pixabay key: generated animated backgrounds will be used",
                         blocking=False))

    if not youtube.oauth_configured():
        checks.append(_check("youtube_oauth", "Google OAuth client configured", False,
                             "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET missing"))
    acct = youtube.get_account(db)
    if acct is None:
        checks.append(_check("youtube", "YouTube channel connected", False, "connect your channel first"))
    elif live:
        res = youtube.verify_connection(db)
        checks.append(_check("youtube", "YouTube channel reachable", res.startswith("ok"), res))
    else:
        checks.append(_check("youtube", "YouTube channel connected", acct.status == "connected", acct.status))

    if youtube.quota_blocked(db):
        checks.append(_check("quota", "YouTube API quota", False, "quota exhausted recently — resets daily",
                             blocking=False))

    checks.append(_check("schedule", "Publishing schedule set", bool(s.publish_times),
                         ", ".join(s.publish_times) + f" ({s.timezone})"))

    ready = all(ch["ok"] for ch in checks if ch["blocking"])
    return {"ready": ready, "checks": checks}
