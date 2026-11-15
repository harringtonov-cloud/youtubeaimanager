"""Auto-playlists: every published video joins one playlist per format, so viewers keep watching (more session
time = more recommendations) and the channel looks like a series. Never fatal for a publication."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..events import log_event
from ..models import Publication, Video
from ..settings_store import get_flag, language_for, get_settings, set_flag
from . import youtube

TITLES = {
    ("short", "en"): "Best Short Stories", ("long", "en"): "Full Stories",
    ("short", "ru"): "Лучшие истории", ("long", "ru"): "Полные истории",
}


def _playlist_id(db: Session, fmt: str, lang: str) -> str:
    key = f"playlist_{fmt}_{lang}"
    pid = get_flag(db, key)
    if pid:
        return str(pid)
    title = TITLES.get((fmt, lang), TITLES[(fmt, "en")])
    r = youtube.api(db, "POST", f"{youtube.API}/playlists", params={"part": "snippet,status"},
                    json_body={"snippet": {"title": title, "description": "Watch them all, back to back."},
                               "status": {"privacyStatus": "public"}})
    pid = r["id"]
    set_flag(db, key, pid)
    return pid


def add_to_playlist(db: Session, video: Video, pub: Publication) -> bool:
    """Adds the published video once. Returns True when it was added now."""
    seo = dict(video.seo or {})
    if seo.get("playlist_added") or not pub.youtube_video_id:
        return False
    s = get_settings(db)
    fmt = "long" if video.format == "long" else "short"
    lang = language_for(s, video).lower().split("-")[0]
    pid = _playlist_id(db, fmt, lang)
    youtube.api(db, "POST", f"{youtube.API}/playlistItems", params={"part": "snippet"},
                json_body={"snippet": {"playlistId": pid,
                                       "resourceId": {"kind": "youtube#video", "videoId": pub.youtube_video_id}}})
    seo["playlist_added"] = True
    video.seo = seo
    db.commit()
    log_event("playlists", f"added to playlist {pid}", video_id=video.id)
    return True
