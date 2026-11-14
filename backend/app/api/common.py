from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..models import AnalyticsSnapshot, Idea, LogEntry, Publication, Video
from ..services import first_comment


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def idea_dict(i: Idea) -> dict[str, Any]:
    return {
        "id": i.id,
        "topic": i.topic,
        "angle": i.angle,
        "hook": i.hook,
        "keywords": i.keywords or [],
        "category": i.category,
        "hook_style": i.hook_style,
        "source": i.source,
        "trend_signal": i.trend_signal,
        "score": round(i.score or 0.0, 2),
        "status": i.status,
        "created_at": iso(i.created_at),
        "used_at": iso(i.used_at),
        "video_id": i.video.id if i.video else None,
    }


def publication_dict(p: Optional[Publication]) -> Optional[dict[str, Any]]:
    if p is None:
        return None
    return {
        "id": p.id,
        "youtube_video_id": p.youtube_video_id,
        "url": p.url,
        "status": p.status,
        "privacy": p.privacy,
        "publish_at": iso(p.publish_at),
        "published_at": iso(p.published_at),
        "thumbnail_set": p.thumbnail_set,
        "upload_attempts": p.upload_attempts,
        "last_error": p.last_error,
        "comment": first_comment.public_status(p.video),
    }


def video_dict(v: Video, full: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": v.id,
        "title": v.title or (v.idea.topic if v.idea else f"Video #{v.id}"),
        "topic": v.idea.topic if v.idea else None,
        "status": v.status,
        "processing_stage": v.processing_stage,
        "scheduled_at": iso(v.scheduled_at),
        "duration_seconds": v.duration_seconds,
        "attempts": v.attempts,
        "error": v.error,
        "failed_stage": v.failed_stage,
        "approved": v.approved,
        "has_video": bool(v.video_key),
        "has_thumbnail": bool(v.thumbnail_key),
        "created_at": iso(v.created_at),
        "updated_at": iso(v.updated_at),
        "publication": publication_dict(v.publication),
    }
    if full:
        d["script"] = v.script
        d["seo"] = v.seo
        d["assets"] = v.assets
    return d


def snapshot_dict(s: Optional[AnalyticsSnapshot]) -> Optional[dict[str, Any]]:
    if s is None:
        return None
    return {
        "fetched_at": iso(s.fetched_at),
        "views": s.views,
        "likes": s.likes,
        "comments": s.comments,
        "shares": s.shares,
        "impressions": s.impressions,
        "ctr": s.ctr,
        "avg_view_duration": s.avg_view_duration,
        "avg_view_percentage": s.avg_view_percentage,
        "estimated_minutes": s.estimated_minutes,
        "subs_gained": s.subs_gained,
    }


def log_dict(l: LogEntry) -> dict[str, Any]:
    return {
        "id": l.id,
        "ts": iso(l.ts),
        "level": l.level,
        "component": l.component,
        "message": l.message,
        "data": l.data,
        "video_id": l.video_id,
    }
