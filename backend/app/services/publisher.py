"""YouTube Publisher: resumable upload, thumbnail, strict duplicate protection, safe retries.

Duplicate protection (defence in depth):
  1. `videos.content_hash` and `ideas.fingerprint` are unique (no two identical videos are ever produced)
  2. `publications.video_id` and `publications.youtube_video_id` are unique
  3. the stage lock (compare-and-set) lets only one worker publish a video at a time
  4. the YouTube id is saved to the DB the moment the upload finishes, *before* any follow-up step
  5. before every retry of an interrupted upload, the channel's uploads are searched for a copy that
     may already exist (title + upload time) and that copy is adopted instead of uploading again
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..events import log_event
from ..models import PUBLISHING, READY, Publication, Video, utcnow
from ..settings_store import get_settings
from ..stages import Deferred, PermanentError, heartbeat
from ..storage import get_storage
from . import youtube
from .youtube import QuotaExceeded, YouTubeError

MAX_AUTO_PER_24H_EXTRA = 1  # auto-publish never exceeds videos_per_day + this within 24h (flood guard)


def _eligible(video: Video, s) -> bool:
    return bool(video.approved or (s.auto_publish_enabled and not s.require_manual_approval))


def due_videos(db: Session) -> list[int]:
    """Videos that should be published now (at most one per scheduler tick)."""
    if not youtube.is_connected(db) or youtube.quota_blocked(db):
        return []
    s = get_settings(db)
    now = utcnow()
    rows = db.execute(
        select(Video).where(Video.status == READY, Video.scheduled_at.is_not(None), Video.scheduled_at <= now,
                            Video.processing_stage.is_(None)).order_by(Video.scheduled_at)
    ).scalars().all()
    rows = [v for v in rows if _eligible(v, s)]
    if not rows:
        return []
    recent = len(db.execute(select(Publication.id).where(Publication.created_at > now - timedelta(hours=24))).all())
    out: list[int] = []
    for v in rows:
        manual = v.approved and not (s.auto_publish_enabled and not s.require_manual_approval)
        if not manual and recent >= s.videos_per_day + MAX_AUTO_PER_24H_EXTRA:
            continue
        out.append(v.id)
        break  # one at a time; the next tick picks up the next one
    return out


def _metadata(video: Video, s) -> tuple[dict, dict]:
    seo = video.seo or {}
    title = (seo.get("title") or video.title or "Short")[:100]
    snippet = {
        "title": title,
        "description": (seo.get("description") or "")[:4900],
        "tags": seo.get("tags") or [],
        "categoryId": str(seo.get("category_id") or s.category_id or "27"),
        "defaultLanguage": s.language,
        "defaultAudioLanguage": s.language,
    }
    status = {
        "privacyStatus": s.privacy_status,
        "selfDeclaredMadeForKids": bool(s.made_for_kids),
        "embeddable": True,
        "publicStatsViewable": True,
    }
    if s.synthetic_media_disclosure:
        status["containsSyntheticMedia"] = True
    return snippet, status


def publish_video(db: Session, video: Video) -> None:
    s = get_settings(db)

    # ---- gates: nothing here is a failure, the video just stays READY until conditions are met
    if not _eligible(video, s):
        raise Deferred("auto-publish is off and the video is not approved")
    if not youtube.is_connected(db):
        raise Deferred("YouTube is not connected")
    if youtube.quota_blocked(db):
        raise Deferred("YouTube API quota exhausted, waiting")
    if not video.video_key or not video.seo:
        raise PermanentError("video is not fully prepared (missing file or SEO)")

    pub = db.execute(select(Publication).where(Publication.video_id == video.id)).scalar_one_or_none()
    if pub is None:
        pub = Publication(video_id=video.id, status="uploading", privacy=s.privacy_status, title=video.title)
        db.add(pub)
        try:
            db.commit()
        except IntegrityError:  # another worker created it first
            db.rollback()
            raise Deferred("publication already being handled")
    if pub.status == "published" and pub.youtube_video_id:
        return  # already done (re-delivered task) -> the stage completes without uploading again

    video.status = PUBLISHING
    db.commit()

    snippet, status = _metadata(video, s)
    try:
        # ---- step 1: make sure the video exists on YouTube exactly once
        if not pub.youtube_video_id:
            if pub.attempt_started_at is not None:  # an earlier attempt may have uploaded before crashing
                existing = youtube.find_recent_upload(db, snippet["title"], pub.attempt_started_at)
                if existing:
                    log_event("publisher", f"adopted already-uploaded video {existing} (no duplicate upload)",
                              level="WARNING", video_id=video.id)
                    pub.youtube_video_id = existing
            if not pub.youtube_video_id:
                pub.attempt_started_at = utcnow()
                pub.upload_attempts = (pub.upload_attempts or 0) + 1
                pub.privacy = s.privacy_status
                pub.title = snippet["title"]
                db.commit()
                work = Path(tempfile.mkdtemp(prefix=f"yam-pub-{video.id}-"))
                try:
                    local = work / "video.mp4"
                    get_storage().get_file(video.video_key, local)
                    heartbeat(db, video)
                    vid = youtube.upload_video(db, local, snippet, status,
                                               on_progress=lambda *_: heartbeat(db, video))
                finally:
                    shutil.rmtree(work, ignore_errors=True)
                pub.youtube_video_id = vid
            # persist the id immediately - this is the duplicate-protection checkpoint
            pub.url = f"https://youtube.com/shorts/{pub.youtube_video_id}"
            pub.status = "published"
            pub.published_at = pub.published_at or utcnow()
            pub.last_error = None
            db.commit()
            log_event("publisher", f"published: {pub.url}", video_id=video.id, youtube_id=pub.youtube_video_id)

        # ---- step 2: custom thumbnail (non-fatal)
        if not pub.thumbnail_set and video.thumbnail_key:
            work = Path(tempfile.mkdtemp(prefix=f"yam-thumb-{video.id}-"))
            try:
                img = work / "thumb.jpg"
                get_storage().get_file(video.thumbnail_key, img)
                youtube.set_thumbnail(db, pub.youtube_video_id, img)
                pub.thumbnail_set = True
                db.commit()
            except Exception as e:
                log_event("publisher", f"thumbnail not set (video is live): {e}", level="WARNING", video_id=video.id)
            finally:
                shutil.rmtree(work, ignore_errors=True)
        # ---- step 3: engagement question as the first comment (non-fatal, tried once)
        seo = video.seo or {}
        if s.first_comment_enabled and seo.get("comment") and not seo.get("comment_tried"):
            video.seo = {**seo, "comment_tried": True}
            db.commit()
            try:
                youtube.post_comment(db, pub.youtube_video_id, seo["comment"])
                log_event("publisher", "first comment posted", video_id=video.id)
            except Exception as e:
                log_event("publisher", f"first comment not posted: {e}", level="WARNING", video_id=video.id)
    except QuotaExceeded as e:
        db.rollback()
        youtube.block_for_quota(db)
        raise Deferred(str(e))
    except YouTubeError as e:
        db.rollback()
        pub = db.get(Publication, pub.id)
        pub.last_error = str(e)[:500]
        db.commit()
        if e.reason in ("uploadLimitExceeded",):
            youtube.block_for_quota(db, hours=12, reason="upload_limit")
            raise Deferred(str(e))
        if e.transient:
            raise  # backoff retry (stage machinery)
        raise PermanentError(str(e))
