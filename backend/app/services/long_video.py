"""Plans the LONG video: one per day, the full 16:9 version of a Short that is already on YouTube.

The Short goes out first (it is the hook that finds viewers); about 24 hours later the long version of the same story
follows. Production starts about an hour after the Short is published, because a long video takes a while to make.
The Short and the long video are separate rows in `videos` (format 'short' / 'long'); the long one points to its
Short through `parent_video_id`.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..events import log_event
from ..models import CANCELLED, AnalyticsSnapshot, Publication, Video, utcnow
from ..settings_store import get_settings
from . import llm

START_AFTER = timedelta(hours=1)     # start making the long video this long after the Short went live
PUBLISH_AFTER = timedelta(hours=24)  # and publish it a day after the Short
LOOKBACK = timedelta(days=3)         # only Shorts newer than this are expanded
ONE_PER = timedelta(hours=20)        # at most one long video per day
LEAD = timedelta(minutes=45)


def _best_candidate(db: Session, now) -> Optional[Video]:
    """The Short to expand: most viewed of the eligible ones (or simply the newest while there are no numbers)."""
    rows = db.execute(
        select(Video, Publication)
        .join(Publication, Publication.video_id == Video.id)
        .where(Video.format == "short", Publication.status == "published", Publication.youtube_video_id.is_not(None),
               Publication.published_at <= now - START_AFTER, Publication.published_at > now - LOOKBACK,
               Video.script.is_not(None))
        .order_by(Publication.published_at.desc())
    ).all()
    taken = {pid for (pid,) in db.execute(select(Video.parent_video_id).where(Video.format == "long",
                                                                               Video.parent_video_id.is_not(None))).all()}
    best: tuple[int, Video] | None = None
    for v, pub in rows:
        if v.id in taken or not isinstance(v.script, dict) or not v.script.get("voiceover"):
            continue
        views = db.execute(select(func.max(AnalyticsSnapshot.views)).where(AnalyticsSnapshot.publication_id == pub.id)
                           ).scalar() or 0
        if best is None or views > best[0]:  # rows are newest first: ties keep the newest
            best = (views, v)
    return best[1] if best else None


def plan_long_videos(db: Session) -> list[int]:
    """Create the next long video if one is due. Returns the ids of new videos (0 or 1)."""
    s = get_settings(db)
    if not s.long_videos_enabled or not llm.is_configured():
        return []
    now = utcnow()
    recent = db.execute(select(Video.id).where(Video.format == "long", Video.status != CANCELLED,
                                               Video.created_at > now - ONE_PER)).first()
    if recent:
        return []
    parent = _best_candidate(db, now)
    if parent is None:
        return []
    pub = parent.publication
    when = max(pub.published_at + PUBLISH_AFTER, now + LEAD)
    video = Video(format="long", parent_video_id=parent.id, title=(parent.title or "")[:300], scheduled_at=when)
    db.add(video)
    db.commit()
    log_event("long_video", f"planned the long version of '{parent.title}'", video_id=video.id,
              parent_video_id=parent.id, scheduled_at=when.isoformat())
    return [video.id]
