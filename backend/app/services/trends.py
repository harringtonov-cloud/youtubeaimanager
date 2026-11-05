"""Free trend signals. Every source is best-effort: failures are logged and never stop the Director."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ..config import get_config
from ..events import get_logger
from ..models import utcnow

log = get_logger("trends")
_UA = {"User-Agent": "YouTubeAIManager/1.0 (trend research)"}

_WIKI_SKIP = re.compile(r"^(Main_Page|Special:|Wikipedia:|Portal:|Help:|File:|Category:|Talk:|-$)", re.I)


def _google_trends(region: str) -> list[dict[str, Any]]:
    r = httpx.get("https://trends.google.com/trending/rss", params={"geo": region}, headers=_UA, timeout=10,
                  follow_redirects=True)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        traffic = ""
        for child in item:
            if child.tag.endswith("approx_traffic"):
                traffic = (child.text or "").strip()
        if title:
            out.append({"source": f"google_trends:{region}", "title": title, "signal": traffic})
    return out[:15]


def _wikipedia_top() -> list[dict[str, Any]]:
    d = utcnow() - timedelta(days=2)
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/"
           f"{d:%Y}/{d:%m}/{d:%d}")
    r = httpx.get(url, headers=_UA, timeout=10)
    r.raise_for_status()
    arts = r.json()["items"][0]["articles"]
    out = []
    for a in arts:
        t = a["article"]
        if _WIKI_SKIP.match(t):
            continue
        out.append({"source": "wikipedia_top", "title": t.replace("_", " "), "signal": f"{a['views']} views"})
        if len(out) >= 20:
            break
    return out


def _youtube_popular(db: Session, region: str) -> list[dict[str, Any]]:
    c = get_config()
    params = {"part": "snippet,statistics", "chart": "mostPopular", "regionCode": region, "maxResults": 20}
    if c.youtube_api_key:
        r = httpx.get("https://www.googleapis.com/youtube/v3/videos", params={**params, "key": c.youtube_api_key},
                      timeout=10)
        r.raise_for_status()
        data = r.json()
    else:
        from . import youtube

        if not youtube.is_connected(db):
            return []
        data = youtube.api(db, "GET", f"{youtube.API}/videos", params=params, timeout=15)
    return [
        {"source": f"youtube_popular:{region}", "title": it["snippet"]["title"],
         "signal": f"{it.get('statistics', {}).get('viewCount', '?')} views"}
        for it in data.get("items", [])
    ]


def collect_signals(db: Session, regions: list[str], limit: int = 40) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    jobs = [("wikipedia", lambda: _wikipedia_top())]
    for rg in (regions or ["US"])[:3]:
        jobs.append((f"google_trends {rg}", lambda rg=rg: _google_trends(rg)))
        jobs.append((f"youtube_popular {rg}", lambda rg=rg: _youtube_popular(db, rg)))
    for name, fn in jobs:
        try:
            signals.extend(fn())
        except Exception as e:
            log.warning("trend source %s failed: %s", name, e)
    seen, uniq = set(), []
    for s in signals:
        k = s["title"].lower()
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq[:limit]
