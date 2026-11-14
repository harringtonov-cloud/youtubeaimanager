"""AI Analytics: collect YouTube metrics, score videos, learn what works, write guidance for the Director."""
from __future__ import annotations

import statistics
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..events import get_logger, log_event
from ..models import AnalyticsSnapshot, Insight, Publication, TopicPerformance, utcnow
from ..settings_store import get_flag, get_settings, set_flag
from . import llm, youtube
from .text_utils import normalize

log = get_logger("analytics")

MIN_VIDEOS_TO_LEARN = 2
MATURITY = timedelta(hours=12)  # younger videos have too little data to judge


# --------------------------------------------------------------------------- collection
def _analytics_rows(db: Session, ids: list[str], start: str, end: str, metrics: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    names = metrics.split(",")
    for i in range(0, len(ids), 100):
        part = ids[i: i + 100]
        data = youtube.analytics_report(db, start=start, end=end, metrics=metrics, dimensions="video",
                                        filters="video==" + ",".join(part), sort="-views", max_results=200)
        for row in data.get("rows", []):
            out[row[0]] = {n: row[j + 1] for j, n in enumerate(names)}
    return out


def sync_analytics(db: Session) -> str:
    if not youtube.is_connected(db):
        return "skipped: YouTube not connected"
    pubs = db.execute(
        select(Publication).where(Publication.status == "published", Publication.youtube_video_id.is_not(None))
        .order_by(Publication.published_at.desc()).limit(300)
    ).scalars().all()
    if not pubs:
        return "no published videos yet"
    ids = [p.youtube_video_id for p in pubs]
    now = utcnow()

    details = youtube.video_details(db, ids)  # public counters, always available
    start = (min(p.published_at for p in pubs if p.published_at) - timedelta(days=1)).date().isoformat()
    end = now.date().isoformat()

    core: dict[str, dict[str, float]] = {}
    try:
        core = _analytics_rows(
            db, ids, start, end,
            "views,likes,comments,shares,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained")
    except youtube.YouTubeError as e:
        log.warning("YouTube Analytics core report unavailable: %s", e)

    imp: dict[str, dict[str, float]] = {}
    if not get_flag(db, "analytics_no_impressions", False):
        try:
            imp = _analytics_rows(db, ids, start, end, "videoThumbnailImpressions,videoThumbnailImpressionsClickRate")
        except youtube.YouTubeError as e:
            if e.status in (400, 403):
                set_flag(db, "analytics_no_impressions", True)  # metric not offered for this channel/API
                log.info("impressions metrics not available: %s", e)

    saved = missing = 0
    for p in pubs:
        d = details.get(p.youtube_video_id)
        if d is None:
            p.last_error = "video not found on YouTube (deleted or private?)"
            missing += 1
            continue
        st = d.get("statistics", {})
        a = core.get(p.youtube_video_id, {})
        i = imp.get(p.youtube_video_id, {})
        snap = AnalyticsSnapshot(
            publication_id=p.id,
            views=int(a.get("views", st.get("viewCount", 0)) or 0),
            likes=int(a.get("likes", st.get("likeCount", 0)) or 0),
            comments=int(a.get("comments", st.get("commentCount", 0)) or 0),
            shares=int(a["shares"]) if "shares" in a else None,
            impressions=int(i["videoThumbnailImpressions"]) if "videoThumbnailImpressions" in i else None,
            ctr=float(i["videoThumbnailImpressionsClickRate"]) if "videoThumbnailImpressionsClickRate" in i else None,
            avg_view_duration=float(a["averageViewDuration"]) if "averageViewDuration" in a else None,
            avg_view_percentage=float(a["averageViewPercentage"]) if "averageViewPercentage" in a else None,
            estimated_minutes=float(a["estimatedMinutesWatched"]) if "estimatedMinutesWatched" in a else None,
            subs_gained=int(a["subscribersGained"]) if "subscribersGained" in a else None,
        )
        # one snapshot per ~2h is plenty; avoid bloating the table
        last = db.execute(select(AnalyticsSnapshot).where(AnalyticsSnapshot.publication_id == p.id)
                          .order_by(AnalyticsSnapshot.fetched_at.desc()).limit(1)).scalar_one_or_none()
        if last and last.fetched_at > now - timedelta(hours=2):
            for f in ("views", "likes", "comments", "shares", "impressions", "ctr", "avg_view_duration",
                      "avg_view_percentage", "estimated_minutes", "subs_gained"):
                setattr(last, f, getattr(snap, f))
            last.fetched_at = now
        else:
            db.add(snap)
        saved += 1
    db.commit()
    youtube.verify_connection(db)  # refresh subscriber/video counts as a side effect
    msg = f"snapshots={saved} missing={missing} analytics_api={'yes' if core else 'public-counters-only'}"
    log_event("analytics", f"analytics synced: {msg}")
    return msg


# --------------------------------------------------------------------------- learning
def percentile_scores(values: list[float]) -> list[float]:
    """0..100 percentile rank of each value (ties share the average rank). Pure, easy to test."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [50.0]
    out = []
    for v in values:
        below = sum(1 for x in values if x < v)
        equal = sum(1 for x in values if x == v)
        out.append(100.0 * (below + (equal - 1) / 2.0) / (n - 1))
    return out


def video_scores(rows: list[dict[str, Any]]) -> list[float]:
    """Composite 0..100: views (40%), retention (45%), engagement (15%) - each relative to the channel.

    Retention weighs most: YouTube spreads Shorts that people watch to the end (and re-watch)."""
    views = percentile_scores([r["views"] for r in rows])
    ret = percentile_scores([r["retention"] for r in rows])
    eng = percentile_scores([r["engagement"] for r in rows])
    return [0.4 * v + 0.45 * r + 0.15 * e for v, r, e in zip(views, ret, eng)]


def _collect(db: Session) -> list[dict[str, Any]]:
    pubs = db.execute(
        select(Publication).where(Publication.status == "published")
        .options(selectinload(Publication.snapshots), selectinload(Publication.video))
    ).scalars().all()
    now = utcnow()
    rows = []
    for p in pubs:
        if not p.snapshots or not p.published_at or p.published_at > now - MATURITY:
            continue
        s = max(p.snapshots, key=lambda x: x.fetched_at)
        idea = p.video.idea if p.video else None
        rows.append({
            "pub": p, "title": p.title, "views": s.views,
            "retention": s.avg_view_percentage or 0.0,
            "engagement": (s.likes + s.comments) / s.views if s.views else 0.0,
            "category": normalize(idea.category) if idea else "",
            "hook_style": normalize(idea.hook_style) if idea else "",
            "keywords": [normalize(k) for k in (idea.keywords or [])] if idea else [],
        })
    return rows


def learn(db: Session) -> str:
    rows = _collect(db)
    if len(rows) < MIN_VIDEOS_TO_LEARN:
        return f"not enough data yet ({len(rows)}/{MIN_VIDEOS_TO_LEARN} mature videos)"
    scores = video_scores(rows)
    for r, sc in zip(rows, scores):
        r["score"] = sc

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        if r["category"]:
            groups.setdefault(("category", r["category"]), []).append(r)
        if r["hook_style"]:
            groups.setdefault(("hook_style", r["hook_style"]), []).append(r)
        for k in set(r["keywords"]):
            if k:
                groups.setdefault(("keyword", k), []).append(r)

    existing = {(t.kind, t.key): t for t in db.execute(select(TopicPerformance)).scalars().all()}
    for (kind, key), items in groups.items():
        t = existing.get((kind, key[:120])) or TopicPerformance(kind=kind, key=key[:120])
        t.videos_count = len(items)
        t.avg_views = statistics.fmean(i["views"] for i in items)
        t.avg_retention = statistics.fmean(i["retention"] for i in items)
        t.avg_engagement = statistics.fmean(i["engagement"] for i in items)
        t.score = statistics.fmean(i["score"] for i in items)
        if t.id is None:
            db.add(t)
    db.commit()

    ranked = sorted(rows, key=lambda r: r["score"], reverse=True)
    data = {
        "videos_analyzed": len(rows),
        "top": [{"title": r["title"], "views": r["views"], "retention": round(r["retention"], 1),
                 "category": r["category"], "hook_style": r["hook_style"]} for r in ranked[:3]],
        "bottom": [{"title": r["title"], "views": r["views"], "retention": round(r["retention"], 1),
                    "category": r["category"], "hook_style": r["hook_style"]} for r in ranked[-3:]],
        "avg_views": round(statistics.fmean(r["views"] for r in rows), 1),
        "median_retention": round(statistics.median(r["retention"] for r in rows), 1),
    }
    summary = _summarize(db, data, groups)
    db.add(Insight(summary=summary, data=data))
    db.commit()
    log_event("analytics", f"learning updated from {len(rows)} videos", topics=len(groups))
    return f"videos={len(rows)} topics={len(groups)}"


def _summarize(db: Session, data: dict[str, Any], groups: dict) -> str:
    top = data["top"][0] if data["top"] else None
    fallback = (f"Across {data['videos_analyzed']} videos the average is {data['avg_views']} views and "
                f"{data['median_retention']}% median retention. "
                + (f"Best performer: \"{top['title']}\" ({top['views']} views, category '{top['category']}', hook "
                   f"'{top['hook_style']}'). Make more videos in that direction while keeping topics fresh."
                   if top else ""))
    if not llm.is_configured():
        return fallback
    try:
        perf = sorted(((k, statistics.fmean(i['score'] for i in v), len(v)) for k, v in groups.items()),
                      key=lambda x: x[1], reverse=True)
        best = "; ".join(f"{k[0]}:{k[1]} score {s:.0f} (n={n})" for k, s, n in perf[:6])
        worst = "; ".join(f"{k[0]}:{k[1]} score {s:.0f} (n={n})" for k, s, n in perf[-4:])
        text = llm.chat(
            [{"role": "system", "content": "You are a YouTube Shorts growth analyst. Be concrete and brief."},
             {"role": "user", "content": (
                 f"Channel data: {data}\nBest groups: {best}\nWeakest groups: {worst}\n\n"
                 "Write 3-5 sentences of actionable guidance for the idea generator and scriptwriter: what to do more "
                 "of, what to avoid, which hook styles/lengths to try. Mention small sample sizes honestly. "
                 "Plain text, no markdown.")}],
            temperature=0.4, max_tokens=400, timeout=60)
        return text.strip()[:1500]
    except Exception as e:
        log.warning("insight summary via LLM failed: %s", e)
        return fallback
