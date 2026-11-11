"""YouTube Data API v3 + OAuth 2.0 (authorization code flow with PKCE, offline access).

Raw HTTP on purpose (httpx): full control over retries, resumable uploads and error classification.
The refresh token is stored encrypted (SECRET_KEY) in `youtube_accounts`; access tokens live in memory only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_config
from ..crypto import decrypt, encrypt
from ..events import get_logger, log_event
from ..models import OAuthState, YouTubeAccount, utcnow
from ..settings_store import get_flag, get_settings, set_flag, update_settings
from ..stages import Deferred, PermanentError

log = get_logger("youtube")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"
ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"

SCOPES = [
    "https://www.googleapis.com/auth/youtube",              # upload, thumbnails, channel + playlist read
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
CHUNK = 16 * 256 * 1024  # 4 MiB, must be a multiple of 256 KiB

QUOTA_FLAG = "yt_quota_blocked_until"


class YouTubeError(Exception):
    def __init__(self, message: str, status: int = 0, reason: str = ""):
        super().__init__(message)
        self.status = status
        self.reason = reason

    @property
    def transient(self) -> bool:
        return self.status in (0, 408, 429) or self.status >= 500


class QuotaExceeded(YouTubeError):
    pass


# --------------------------------------------------------------------------- OAuth
def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def oauth_configured() -> bool:
    c = get_config()
    return bool(c.google_client_id and c.google_client_secret)


def build_auth_url(db: Session) -> str:
    c = get_config()
    if not oauth_configured():
        raise PermanentError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not configured")
    state = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    db.add(OAuthState(state=state, code_verifier=verifier))
    db.commit()
    params = {
        "client_id": c.google_client_id,
        "redirect_uri": c.oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # always return a refresh token
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _post_token(data: dict[str, str]) -> dict[str, Any]:
    try:
        r = httpx.post(TOKEN_URL, data=data, timeout=30)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise YouTubeError(f"token endpoint unreachable: {e}") from e
    if r.status_code != 200:
        try:
            body = r.json()
        except Exception:
            body = {}
        err = body.get("error", "")
        desc = body.get("error_description", r.text[:200])
        if err == "invalid_grant":
            raise YouTubeError(f"invalid_grant: {desc}", status=400, reason="invalid_grant")
        raise YouTubeError(f"token endpoint error {r.status_code}: {err} {desc}", status=r.status_code, reason=err)
    return r.json()


def handle_callback(db: Session, code: str, state: str) -> YouTubeAccount:
    """Exchange the authorization code, verify the channel and store the encrypted refresh token."""
    c = get_config()
    row = db.get(OAuthState, state)
    if row is None or row.created_at < utcnow() - timedelta(minutes=15):
        raise PermanentError("OAuth state is unknown or expired — start the connection again")
    verifier = row.code_verifier
    db.delete(row)
    db.commit()

    tok = _post_token(
        {
            "client_id": c.google_client_id,
            "client_secret": c.google_client_secret,
            "code": code,
            "code_verifier": verifier or "",
            "grant_type": "authorization_code",
            "redirect_uri": c.oauth_redirect_uri,
        }
    )
    refresh = tok.get("refresh_token")
    access = tok["access_token"]
    if not refresh:
        existing = db.execute(select(YouTubeAccount).order_by(YouTubeAccount.id.desc()).limit(1)).scalar_one_or_none()
        if existing is None:
            raise PermanentError(
                "Google did not return a refresh token. Remove the app at https://myaccount.google.com/permissions "
                "and connect again."
            )
        refresh = decrypt(existing.refresh_token_enc)

    granted = set((tok.get("scope") or "").split())
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        raise PermanentError("Not all permissions were granted (tick every checkbox on the Google consent screen): "
                             + ", ".join(m.rsplit("/", 1)[-1] for m in missing))

    channel = _fetch_channel(access)
    acct = db.execute(select(YouTubeAccount).where(YouTubeAccount.channel_id == channel["id"])).scalar_one_or_none()
    if acct is None:
        acct = YouTubeAccount(channel_id=channel["id"], refresh_token_enc=encrypt(refresh))
        db.add(acct)
    acct.refresh_token_enc = encrypt(refresh)
    acct.scopes = " ".join(sorted(granted))
    acct.status = "connected"
    acct.last_error = None
    acct.connected_at = utcnow()
    _apply_channel(acct, channel)
    db.commit()
    _TOKEN_CACHE.clear()
    log_event("youtube", f"YouTube channel connected: {acct.channel_title}", channel_id=acct.channel_id)
    return acct


# --------------------------------------------------------------------------- tokens
_TOKEN_CACHE: dict[int, tuple[str, float]] = {}


def get_account(db: Session) -> Optional[YouTubeAccount]:
    return db.execute(select(YouTubeAccount).order_by(YouTubeAccount.id.desc()).limit(1)).scalar_one_or_none()


def is_connected(db: Session) -> bool:
    a = get_account(db)
    return bool(a and a.status == "connected")


def _mark_reauth(db: Session, acct: YouTubeAccount, why: str) -> None:
    acct.status = "needs_reauth"
    acct.last_error = why[:500]
    db.commit()
    _TOKEN_CACHE.pop(acct.id, None)
    s = get_settings(db)
    if s.auto_publish_enabled:
        update_settings(db, {"auto_publish_enabled": False})
        log_event("youtube", "auto-publish PAUSED: YouTube authorization was revoked or expired", level="ERROR")
    log_event("youtube", f"YouTube needs re-authorization: {why}", level="ERROR")


def access_token(db: Session, force_refresh: bool = False) -> str:
    acct = get_account(db)
    if acct is None:
        raise PermanentError("YouTube is not connected")
    if acct.status == "needs_reauth":
        raise PermanentError("YouTube authorization expired — reconnect the channel in the dashboard")
    cached = _TOKEN_CACHE.get(acct.id)
    if cached and not force_refresh and cached[1] > time.time() + 60:
        return cached[0]
    c = get_config()
    try:
        tok = _post_token(
            {
                "client_id": c.google_client_id,
                "client_secret": c.google_client_secret,
                "refresh_token": decrypt(acct.refresh_token_enc),
                "grant_type": "refresh_token",
            }
        )
    except YouTubeError as e:
        if e.reason == "invalid_grant":
            _mark_reauth(db, acct, str(e))
            raise PermanentError("YouTube authorization was revoked — reconnect the channel") from e
        raise
    _TOKEN_CACHE[acct.id] = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)))
    return tok["access_token"]


# --------------------------------------------------------------------------- HTTP helpers
def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    reason, msg = "", resp.text[:300]
    try:
        err = resp.json().get("error", {})
        if isinstance(err, dict):
            msg = err.get("message", msg)
            errs = err.get("errors") or []
            reason = (errs[0].get("reason") if errs else "") or err.get("status", "")
    except Exception:
        pass
    if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded") or "quota" in msg.lower():
        raise QuotaExceeded(f"YouTube quota exceeded: {msg}", resp.status_code, reason)
    raise YouTubeError(f"YouTube API {resp.status_code} {reason}: {msg}", resp.status_code, reason)


def api(db: Session, method: str, url: str, *, params: Optional[dict] = None, json_body: Any = None,
        timeout: float = 60.0) -> dict[str, Any]:
    """Authenticated call with one automatic token refresh on 401."""
    for attempt in (0, 1):
        token = access_token(db, force_refresh=bool(attempt))
        try:
            r = httpx.request(method, url, params=params, json=json_body,
                              headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise YouTubeError(f"network error: {type(e).__name__}: {e}") from e
        if r.status_code == 401 and attempt == 0:
            continue
        _raise_for(r)
        return r.json() if r.content else {}
    raise YouTubeError("unauthorized", 401)


# --------------------------------------------------------------------------- channel
def _fetch_channel(token: str) -> dict[str, Any]:
    r = httpx.get(f"{API}/channels", params={"part": "snippet,statistics,contentDetails", "mine": "true"},
                  headers={"Authorization": f"Bearer {token}"}, timeout=30)
    _raise_for(r)
    items = r.json().get("items") or []
    if not items:
        raise PermanentError("This Google account has no YouTube channel. Create one at youtube.com and reconnect.")
    return items[0]


def _apply_channel(acct: YouTubeAccount, ch: dict[str, Any]) -> None:
    sn, st = ch.get("snippet", {}), ch.get("statistics", {})
    acct.channel_title = sn.get("title", "")
    acct.thumbnail_url = ((sn.get("thumbnails") or {}).get("default") or {}).get("url")
    acct.subscriber_count = int(st["subscriberCount"]) if st.get("subscriberCount") is not None else None
    acct.video_count = int(st["videoCount"]) if st.get("videoCount") is not None else None
    acct.uploads_playlist_id = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")


def verify_connection(db: Session) -> str:
    """Live check: token refresh + channel read. Updates the account row. Returns a status line."""
    acct = get_account(db)
    if acct is None:
        return "not connected"
    if acct.status == "needs_reauth":
        return "needs re-authorization"
    try:
        ch = _fetch_channel(access_token(db))
    except PermanentError as e:
        acct.last_error = str(e)[:500]
        db.commit()
        return f"error: {e}"
    except YouTubeError as e:
        acct.last_error = str(e)[:500]
        db.commit()
        return f"unreachable: {e}"
    _apply_channel(acct, ch)
    acct.status = "connected"
    acct.last_error = None
    acct.last_verified_at = utcnow()
    db.commit()
    return f"ok: {acct.channel_title}"


def disconnect(db: Session) -> None:
    acct = get_account(db)
    if acct is None:
        return
    try:
        httpx.post("https://oauth2.googleapis.com/revoke", data={"token": decrypt(acct.refresh_token_enc)}, timeout=15)
    except Exception:
        pass  # revocation is best-effort; the local credentials are removed regardless
    db.execute(delete(YouTubeAccount).where(YouTubeAccount.id == acct.id))
    db.commit()
    _TOKEN_CACHE.clear()
    if get_settings(db).auto_publish_enabled:
        update_settings(db, {"auto_publish_enabled": False})
    log_event("youtube", "YouTube channel disconnected by owner", level="WARNING")


# --------------------------------------------------------------------------- quota gate
def quota_blocked(db: Session) -> bool:
    until = get_flag(db, QUOTA_FLAG)
    if not until:
        return False
    try:
        from datetime import datetime

        return datetime.fromisoformat(until) > utcnow()
    except Exception:
        return False


BLOCK_REASON_FLAG = "yt_block_reason"


def block_reason(db: Session) -> str:
    return (get_flag(db, BLOCK_REASON_FLAG) or "quota") if quota_blocked(db) else ""


def block_for_quota(db: Session, hours: float = 3.0, reason: str = "quota") -> None:
    set_flag(db, QUOTA_FLAG, (utcnow() + timedelta(hours=hours)).isoformat())
    set_flag(db, BLOCK_REASON_FLAG, reason)
    what = ("YouTube upload limit of the channel reached (verify the channel by phone: youtube.com/verify)"
            if reason == "upload_limit" else "YouTube API quota exhausted")
    log_event("youtube", f"{what} - publishing paused for {hours:g}h", level="WARNING")


def clear_block(db: Session) -> None:
    set_flag(db, QUOTA_FLAG, "")
    set_flag(db, BLOCK_REASON_FLAG, "")
    log_event("youtube", "publishing pause cleared by owner")


# --------------------------------------------------------------------------- uploads
def find_recent_upload(db: Session, title: str, since) -> Optional[str]:
    """Guard against double uploads: did a video with this title appear in our uploads since `since`?"""
    acct = get_account(db)
    if acct is None or not acct.uploads_playlist_id:
        return None
    data = api(db, "GET", f"{API}/playlistItems",
               params={"part": "snippet,contentDetails", "playlistId": acct.uploads_playlist_id, "maxResults": 15})
    from datetime import datetime

    floor = since - timedelta(minutes=2)
    for it in data.get("items", []):
        sn = it.get("snippet", {})
        if (sn.get("title") or "").strip() != title.strip():
            continue
        try:
            published = datetime.fromisoformat(
                (it.get("contentDetails", {}).get("videoPublishedAt") or sn.get("publishedAt")).replace("Z", "+00:00"))
        except Exception:
            continue
        if published >= floor:
            return it.get("contentDetails", {}).get("videoId") or (sn.get("resourceId") or {}).get("videoId")
    return None


def upload_video(db: Session, file_path: str | Path, snippet: dict[str, Any], status: dict[str, Any],
                 on_progress: Optional[Callable[[int, int], None]] = None) -> str:
    """Resumable upload. Returns the YouTube video id. Raises YouTubeError / QuotaExceeded / PermanentError."""
    path = Path(file_path)
    size = path.stat().st_size
    if size == 0:
        raise PermanentError("video file is empty")
    body = {"snippet": snippet, "status": status}

    def _init(body_: dict) -> str:
        token = access_token(db)
        try:
            r = httpx.post(
                f"{UPLOAD_API}/videos",
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Length": str(size),
                    "X-Upload-Content-Type": "video/mp4",
                },
                content=json.dumps(body_),
                timeout=60,
            )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise YouTubeError(f"network error starting upload: {e}") from e
        if r.status_code == 401:
            access_token(db, force_refresh=True)
            raise YouTubeError("token refreshed, retry", 503)
        _raise_for(r)
        loc = r.headers.get("Location")
        if not loc:
            raise YouTubeError("no upload session URL returned", 502)
        return loc

    try:
        session_url = _init(body)
    except YouTubeError as e:
        # older API surfaces reject the synthetic-media flag: retry once without it
        if e.status == 400 and "containsSyntheticMedia" in str(e):
            body["status"] = {k: v for k, v in status.items() if k != "containsSyntheticMedia"}
            session_url = _init(body)
        else:
            raise

    offset = 0
    errors = 0
    with path.open("rb") as fh:
        while offset < size:
            fh.seek(offset)
            chunk = fh.read(CHUNK)
            end = offset + len(chunk) - 1
            try:
                r = httpx.put(
                    session_url,
                    content=chunk,
                    headers={"Content-Length": str(len(chunk)), "Content-Type": "video/mp4",
                             "Content-Range": f"bytes {offset}-{end}/{size}"},
                    timeout=httpx.Timeout(120, connect=30),
                )
            except (httpx.TimeoutException, httpx.TransportError):
                errors += 1
                if errors > 8:
                    raise YouTubeError("upload interrupted too many times")
                time.sleep(min(2 ** errors, 30))
                offset = _query_offset(session_url, size)
                continue
            if r.status_code in (200, 201):
                vid = r.json().get("id")
                if not vid:
                    raise YouTubeError("upload finished but no video id returned", 502)
                return vid
            if r.status_code == 308:
                rng = r.headers.get("Range")
                offset = int(rng.split("-")[-1]) + 1 if rng else 0
                errors = 0
                if on_progress:
                    on_progress(offset, size)
                continue
            if r.status_code >= 500 or r.status_code == 429:
                errors += 1
                if errors > 8:
                    _raise_for(r)
                time.sleep(min(2 ** errors, 30))
                offset = _query_offset(session_url, size)
                continue
            if r.status_code == 404:
                raise YouTubeError("upload session expired", 503)  # transient: next attempt starts a new session
            _raise_for(r)
    raise YouTubeError("upload loop ended without completion", 502)


def _query_offset(session_url: str, size: int) -> int:
    """Ask the server how many bytes it has so an interrupted upload can resume."""
    try:
        r = httpx.put(session_url, content=b"", headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"},
                      timeout=30)
    except (httpx.TimeoutException, httpx.TransportError):
        return 0
    if r.status_code == 308:
        rng = r.headers.get("Range")
        return int(rng.split("-")[-1]) + 1 if rng else 0
    if r.status_code in (200, 201):
        return size
    return 0


def set_thumbnail(db: Session, youtube_video_id: str, image_path: str | Path) -> None:
    token = access_token(db)
    data = Path(image_path).read_bytes()
    try:
        r = httpx.post(f"{UPLOAD_API}/thumbnails/set", params={"videoId": youtube_video_id},
                       headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"},
                       content=data, timeout=60)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise YouTubeError(f"network error: {e}") from e
    _raise_for(r)


def video_details(db: Session, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Public statistics for up to 50 ids per call."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), 50):
        part = ids[i: i + 50]
        data = api(db, "GET", f"{API}/videos", params={"part": "snippet,statistics,status", "id": ",".join(part)})
        for it in data.get("items", []):
            out[it["id"]] = it
    return out


def analytics_report(db: Session, *, start: str, end: str, metrics: str, dimensions: str = "",
                     filters: str = "", sort: str = "", max_results: int = 200) -> dict[str, Any]:
    params: dict[str, Any] = {"ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": metrics}
    if dimensions:
        params["dimensions"] = dimensions
    if filters:
        params["filters"] = filters
    if sort:
        params["sort"] = sort
    if dimensions:
        params["maxResults"] = max_results
    return api(db, "GET", ANALYTICS_API, params=params)


def raise_deferred_on_quota(db: Session, e: QuotaExceeded) -> None:
    block_for_quota(db)
    raise Deferred(str(e))
