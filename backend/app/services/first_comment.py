"""First comment under a published Short: separate from the upload, retried, duplicate-safe.

Why it is not part of the upload any more: right after an upload YouTube is still processing the video and the
comment call often fails (404 / 403). The old code tried exactly once, logged a warning and never came back.

Now publishing only *schedules* the comment (state in `video.seo["comment_state"]`, no schema change) and a sweep
(`process_pending`, called on every cron tick / runner cycle) posts it with back-off.

Safety nets:
  * a comment is never inserted before a check that the channel has not already written the same text there
  * the row is locked (SELECT ... FOR UPDATE) and claimed (`posting`) before the insert, so two workers
    (API cron + GitHub runner) cannot post twice
  * a missing permission (older connection without the comments scope) is reported once per 6h in the log with
    the exact fix, and never burns the retry attempts
  * a comment problem never touches the video: publishing is already finished when this runs
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import log_event
from ..models import Publication, Video, utcnow
from ..settings_store import get_settings
from ..stages import PermanentError
from . import youtube
from .youtube import QuotaExceeded, YouTubeError

FIRST_DELAY = timedelta(minutes=3)  # let YouTube finish processing the upload
BACKOFF = [timedelta(minutes=m) for m in (5, 15, 45, 120, 360)]
MAX_ATTEMPTS = 6
REAUTH_WAIT = timedelta(hours=6)
QUOTA_WAIT = timedelta(hours=1)
CLAIM_TTL = timedelta(minutes=10)  # a "posting" claim older than this is a crashed worker
MAX_AGE = timedelta(days=7)        # do not go back further than this
PER_SWEEP = 2                      # comment inserts cost 50 quota units each: keep it modest

PENDING, POSTING, POSTED, SKIPPED, FAILED = "pending", "posting", "posted", "skipped", "failed"
FINAL = (POSTED, SKIPPED, FAILED)

RECONNECT_HINT = ("нет разрешения на комментарии: откройте раздел YouTube в панели, нажмите «Отключить», затем "
                  "«Подключить» и отметьте все галочки на экране Google")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(value: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=utcnow().tzinfo)


def state_of(video: Video) -> dict[str, Any]:
    return dict((video.seo or {}).get("comment_state") or {})


def _save(video: Video, state: dict[str, Any]) -> None:
    video.seo = {**(video.seo or {}), "comment_state": state}  # reassign: JSON columns do not track in-place edits


def schedule(video: Video, published_at: Optional[datetime] = None) -> None:
    """Called by the publisher right after a successful upload. Does not talk to YouTube."""
    seo = video.seo or {}
    if not seo.get("comment") or state_of(video):
        return
    when = (published_at or utcnow()) + FIRST_DELAY
    _save(video, {"status": PENDING, "attempts": 0, "next_at": _iso(when), "error": ""})


def public_status(video: Optional[Video]) -> Optional[dict[str, Any]]:
    """What the dashboard shows: status + last error."""
    if video is None:
        return None
    st = state_of(video)
    if not st:
        return None
    return {"status": st.get("status"), "attempts": st.get("attempts", 0), "error": st.get("error") or None}


def _due(video: Video, now: datetime) -> bool:
    seo = video.seo or {}
    if not seo.get("comment"):
        return False
    st = state_of(video)
    if not st:
        # published by the old code: it set `comment_tried` and gave up after one attempt (success unknown).
        # The duplicate check below makes one more try safe.
        return bool(seo.get("comment_tried"))
    status = st.get("status")
    if status in FINAL:
        return False
    if status == POSTING:
        started = _parse(st.get("claimed_at"))
        return started is None or now - started > CLAIM_TTL
    nxt = _parse(st.get("next_at"))
    return nxt is None or nxt <= now


def _candidates(db: Session, now: datetime) -> list[int]:
    rows = db.execute(
        select(Video.id)
        .join(Publication, Publication.video_id == Video.id)
        .where(Publication.status == "published", Publication.youtube_video_id.is_not(None),
               Publication.published_at > now - MAX_AGE)
        .order_by(Publication.published_at.desc())
    ).all()
    out: list[int] = []
    for (vid,) in rows:
        v = db.get(Video, vid)
        if v is not None and _due(v, now):
            out.append(vid)
    return out


def _attempt(db: Session, video_id: int) -> str:
    """One locked attempt for one video. Returns posted | skipped | retry | reauth | quota | busy | failed."""
    now = utcnow()
    # lock the row (Postgres) and re-check: another worker may have just handled it
    video = db.execute(select(Video).where(Video.id == video_id).with_for_update()).scalar_one_or_none()
    if video is None or not _due(video, now) or video.publication is None:
        db.rollback()
        return "busy"
    pub = video.publication
    text = (video.seo or {}).get("comment") or ""
    st = state_of(video) or {"status": PENDING, "attempts": 0}
    attempts = int(st.get("attempts") or 0)
    _save(video, {**st, "status": POSTING, "claimed_at": _iso(now)})
    db.commit()  # the claim is visible to the other workers from here on

    def finish(status: str, **extra: Any) -> None:
        db.refresh(video)
        cur = state_of(video)
        cur.pop("claimed_at", None)
        _save(video, {**cur, "status": status, **extra})
        db.commit()

    try:
        found = youtube.find_own_comment(db, pub.youtube_video_id, text)
        if found:
            finish(POSTED, id=found, error="", attempts=attempts)
            log_event("comment", "first comment is already there (no duplicate)", video_id=video.id)
            return "posted"
        cid = youtube.post_comment(db, pub.youtube_video_id, text)
        finish(POSTED, id=cid, error="", attempts=attempts + 1)
        log_event("comment", "first comment posted", video_id=video.id, youtube_id=pub.youtube_video_id)
        return "posted"
    except QuotaExceeded as e:
        finish(PENDING, next_at=_iso(utcnow() + QUOTA_WAIT), error=str(e)[:300], attempts=attempts)
        log_event("comment", f"first comment postponed (quota): {e}", level="WARNING", video_id=video.id)
        return "quota"
    except YouTubeError as e:
        kind = youtube.classify_comment_error(e)
        err = str(e)[:300]
        if kind == "reauth":
            finish(PENDING, next_at=_iso(utcnow() + REAUTH_WAIT), error=RECONNECT_HINT, attempts=attempts)
            log_event("comment", f"first comment not posted: {RECONNECT_HINT} ({err})", level="ERROR",
                      video_id=video.id)
            return "reauth"
        if kind == "skip":
            finish(SKIPPED, error=err, attempts=attempts + 1)
            log_event("comment", f"first comment skipped for good: {err}", level="WARNING", video_id=video.id)
            return "skipped"
        attempts += 1
        if attempts >= MAX_ATTEMPTS:
            finish(FAILED, error=err, attempts=attempts)
            log_event("comment", f"first comment given up after {attempts} attempts: {err}", level="ERROR",
                      video_id=video.id)
            return "failed"
        wait = BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
        finish(PENDING, next_at=_iso(utcnow() + wait), error=err, attempts=attempts)
        log_event("comment", f"first comment attempt {attempts}/{MAX_ATTEMPTS} failed, next in "
                  f"{int(wait.total_seconds() // 60)} min: {err}", level="WARNING", video_id=video.id)
        return "retry"
    except PermanentError as e:  # channel not connected / authorization revoked: wait, do not burn attempts
        db.rollback()
        finish(PENDING, next_at=_iso(utcnow() + REAUTH_WAIT), error=str(e)[:300], attempts=attempts)
        log_event("comment", f"first comment waits: {e}", level="WARNING", video_id=video.id)
        return "reauth"
    except Exception as e:  # network / anything unexpected: never break the caller, try again later
        db.rollback()
        attempts += 1
        err = f"{type(e).__name__}: {e}"[:300]
        state = FAILED if attempts >= MAX_ATTEMPTS else PENDING
        wait = BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
        finish(state, next_at=_iso(utcnow() + wait), error=err, attempts=attempts)
        log_event("comment", f"first comment attempt {attempts} crashed: {err}", level="WARNING", video_id=video.id)
        return "failed" if state == FAILED else "retry"


def process_pending(db: Session, limit: int = PER_SWEEP) -> dict[str, int]:
    """Post the comments that are due. Safe to call as often as you like (every 5 min is fine)."""
    out: dict[str, int] = {}
    if not get_settings(db).first_comment_enabled:
        return out
    if not youtube.is_connected(db):
        return out
    now = utcnow()
    for vid in _candidates(db, now)[:limit]:
        res = _attempt(db, vid)
        out[res] = out.get(res, 0) + 1
        if res in ("quota", "reauth"):  # the same problem would hit every other video too
            break
    return out
