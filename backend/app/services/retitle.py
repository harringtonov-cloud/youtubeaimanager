"""Second chance for flops: a Short that got few views after a couple of days gets a fresh, stronger title once.
Same video, same URL (so its comments and stats stay); only the title changes. One video per sweep, every 12 h."""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..events import log_event
from ..models import Publication, Video, utcnow
from ..settings_store import get_flag, get_settings, language_for, set_flag
from . import llm, youtube
from .seo import TITLE_MAX
from .text_utils import clamp

INTERVAL = timedelta(hours=12)
MIN_AGE, MAX_AGE = timedelta(days=2), timedelta(days=10)
MIN_VIDEOS = 4  # need a few mature Shorts to know what "weak" means on this channel


def pick(db: Session):
    now = utcnow()
    pubs = db.execute(
        select(Publication).where(Publication.status == "published", Publication.youtube_video_id.is_not(None))
        .options(selectinload(Publication.snapshots), selectinload(Publication.video))
    ).scalars().all()
    rows = []
    for p in pubs:
        if not p.published_at or not p.snapshots or not p.video or p.video.format == "long":
            continue
        if now - p.published_at < MIN_AGE:
            continue
        rows.append((max(p.snapshots, key=lambda x: x.fetched_at).views, p))
    if len(rows) < MIN_VIDEOS:
        return None
    med = median(v for v, _ in rows)
    weak = [(v, p) for v, p in rows if v < med * 0.5 and now - p.published_at <= MAX_AGE
            and not (p.video.seo or {}).get("retitled")]
    return min(weak, key=lambda x: x[0])[1] if weak else None


def process(db: Session) -> str:
    last = get_flag(db, "retitle_last_run")
    if last and utcnow() - datetime.fromisoformat(last) < INTERVAL:
        return "wait"
    set_flag(db, "retitle_last_run", utcnow().isoformat())  # throttle even when nothing to do
    if not llm.is_configured() or not youtube.is_connected(db) or youtube.quota_blocked(db):
        return "skipped"
    p = pick(db)
    if p is None:
        return "none"
    v = p.video
    s = get_settings(db)
    lang = language_for(s, v)
    data = llm.chat_json([
        {"role": "system", "content": "You write YouTube Shorts titles that get clicks without lying."},
        {"role": "user", "content": f"Language: {lang}. This Short underperformed. Old title: {v.title!r}. "
         f"Story: {str((v.script or {}).get('voiceover', ''))[:600]}\nWrite ONE new title (max {TITLE_MAX} characters): "
         'a clear curiosity gap, the main keyword first, different wording from the old one, no clickbait lies, '
         'no ALL CAPS. Return JSON {"title": ""}.'}], temperature=0.8, max_tokens=200)
    new = clamp(str((data or {}).get("title", "")).strip().strip('"'), TITLE_MAX)
    if len(new) < 8 or new.lower() == (v.title or "").lower():
        return "no-title"
    from .publisher import _metadata

    snippet, _ = _metadata(v, s)
    old = snippet["title"]
    snippet["title"] = new
    youtube.api(db, "PUT", f"{youtube.API}/videos", params={"part": "snippet"},
                json_body={"id": p.youtube_video_id, "snippet": snippet})
    seo = dict(v.seo or {})
    seo.update({"retitled": True, "old_title": old, "title": new})
    v.seo, v.title, p.title = seo, new, new
    db.commit()
    log_event("retitle", f"weak Short re-titled: '{old}' -> '{new}'", video_id=v.id)
    return "ok"
