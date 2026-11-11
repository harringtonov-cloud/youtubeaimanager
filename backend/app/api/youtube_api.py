from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..config import get_config
from ..db import get_db
from ..events import log_event
from ..security import require_admin
from ..services import preflight, youtube
from ..stages import PermanentError
from .common import iso

router = APIRouter(prefix="/api", tags=["youtube"])


def _account_dict(db: Session) -> dict:
    a = youtube.get_account(db)
    cfg = get_config()
    base = {
        "oauth_configured": youtube.oauth_configured(),
        "redirect_uri": cfg.oauth_redirect_uri,
        "scopes_required": youtube.SCOPES,
        "quota_blocked": youtube.quota_blocked(db),
        "block_reason": youtube.block_reason(db),
    }
    if a is None:
        return {**base, "connected": False, "status": "not_connected"}
    return {
        **base,
        "connected": a.status == "connected",
        "status": a.status,
        "channel_id": a.channel_id,
        "channel_title": a.channel_title,
        "thumbnail_url": a.thumbnail_url,
        "subscriber_count": a.subscriber_count,
        "video_count": a.video_count,
        "connected_at": iso(a.connected_at),
        "last_verified_at": iso(a.last_verified_at),
        "last_error": a.last_error,
        "scopes": a.scopes.split() if a.scopes else [],
    }


@router.get("/youtube/status", dependencies=[Depends(require_admin)])
def youtube_status(db: Session = Depends(get_db)):
    return _account_dict(db)


@router.post("/youtube/connect", dependencies=[Depends(require_admin)])
def youtube_connect(db: Session = Depends(get_db)):
    try:
        return {"auth_url": youtube.build_auth_url(db)}
    except PermanentError as e:
        raise HTTPException(409, str(e))


@router.get("/youtube/callback")
def youtube_callback(code: str = Query(None), state: str = Query(None), error: str = Query(None),
                     db: Session = Depends(get_db)):
    """Google redirects the browser here. Public route, protected by the one-time `state` + PKCE."""
    front = get_config().frontend_url.rstrip("/") + "/youtube"
    if error or not code or not state:
        return RedirectResponse(f"{front}?{urlencode({'error': error or 'missing code'})}")
    try:
        youtube.handle_callback(db, code, state)
    except (PermanentError, youtube.YouTubeError) as e:
        log_event("youtube", f"OAuth callback failed: {e}", level="ERROR")
        return RedirectResponse(f"{front}?{urlencode({'error': str(e)[:300]})}")
    return RedirectResponse(f"{front}?connected=1")


@router.post("/youtube/resume", dependencies=[Depends(require_admin)])
def resume_publishing(db: Session = Depends(get_db)):
    """Clear the publishing pause (e.g. after verifying the channel by phone, which raises the upload limit)."""
    youtube.clear_block(db)
    return {"ok": True}


@router.post("/youtube/verify", dependencies=[Depends(require_admin)])
def youtube_verify(db: Session = Depends(get_db)):
    result = youtube.verify_connection(db)
    return {"result": result, **_account_dict(db)}


@router.post("/youtube/disconnect", dependencies=[Depends(require_admin)])
def youtube_disconnect(db: Session = Depends(get_db)):
    youtube.disconnect(db)
    return _account_dict(db)


@router.get("/preflight", dependencies=[Depends(require_admin)])
def run_preflight(live: bool = Query(True), db: Session = Depends(get_db)):
    return preflight.run_checks(db, live=live)
