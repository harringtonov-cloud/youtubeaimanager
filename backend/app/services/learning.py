"""Read-side of the learning loop: turns stored analytics into prompt context and idea scores."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Insight, Publication, TopicPerformance, Video
from .text_utils import normalize

LANGUAGE_NAMES = {
    "en": "English", "ru": "Russian", "uz": "Uzbek", "es": "Spanish", "de": "German", "fr": "French",
    "pt": "Portuguese", "it": "Italian", "tr": "Turkish", "ar": "Arabic", "hi": "Hindi", "id": "Indonesian",
    "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "uk": "Ukrainian", "pl": "Polish", "kk": "Kazakh",
}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get((code or "en").lower().split("-")[0], code)


def words_per_second(code: str) -> float:
    c = (code or "en").lower().split("-")[0]
    return {"ru": 2.3, "uz": 2.1, "de": 2.2, "tr": 2.2, "es": 2.7, "pt": 2.7, "it": 2.7, "ja": 3.0, "zh": 3.0,
            "ko": 3.0}.get(c, 2.5)


WINNER_WEIGHTS = (0.40, 0.45, 0.15)  # views per hour, retention (avg % watched), engagement (likes+comments)


def _rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order videos best-first by a composite of views/hour, retention and engagement (each relative to the channel).

    Retention gets the biggest weight: it is what Shorts distribution rewards, and views/hour alone favours luck
    (a lucky publishing slot) over a format that holds the audience. Sets `row["score"]` (0..100)."""
    from .analytics import percentile_scores

    if not rows:
        return rows
    wv, wr, we = WINNER_WEIGHTS
    rate = percentile_scores([r["rate"] for r in rows])
    ret = percentile_scores([float(r["retention"] or 0.0) for r in rows])
    eng = percentile_scores([(r["likes"] + r["comments"]) / r["views"] if r["views"] else 0.0 for r in rows])
    for r, a, b, c in zip(rows, rate, ret, eng):
        r["score"] = wv * a + wr * b + we * c
    return sorted(rows, key=lambda r: (r["score"], r["rate"]), reverse=True)


def top_performers(db: Session, n: int = 3) -> list[dict[str, Any]]:
    """Best videos so far (views/hour + retention + engagement), with their likes, retention and format."""
    from datetime import timedelta

    from ..models import utcnow

    now = utcnow()
    pubs = db.execute(
        select(Publication).where(Publication.status == "published")
        .options(selectinload(Publication.snapshots), selectinload(Publication.video).selectinload(Video.idea))
    ).scalars().all()
    rows = []
    for p in pubs:
        if not p.snapshots or not p.published_at or p.published_at > now - timedelta(hours=3):
            continue
        if p.video and p.video.format == "long":  # long videos have other retention/length: not comparable to Shorts
            continue
        sn = max(p.snapshots, key=lambda x: x.fetched_at)
        if sn.views <= 0:
            continue
        hours = max((now - p.published_at).total_seconds() / 3600.0, 6.0)
        idea = p.video.idea if p.video else None
        rows.append({
            "title": p.title, "views": sn.views, "likes": sn.likes, "comments": sn.comments,
            "retention": sn.avg_view_percentage, "rate": sn.views / hours,
            "topic": idea.topic if idea else "", "angle": getattr(idea, "angle", "") if idea else "",
            "category": idea.category if idea else "", "hook_style": idea.hook_style if idea else "",
            "keywords": list(idea.keywords or []) if idea else [],
        })
    return _rank_rows(rows)[:n]


def weak_performers(db: Session, n: int = 2) -> list[dict[str, Any]]:
    """Worst videos with enough data (3+ mature videos needed), so the writers know what to avoid."""
    rows = top_performers(db, n=1000)
    if len(rows) < 4:
        return []
    return [r for r in rows[-n:] if r["score"] < 35.0]


def digest(db: Session, max_chars: int = 1800) -> str:
    """Human-readable summary of what worked / what didn't, for LLM prompts."""
    parts: list[str] = []
    insight = db.execute(select(Insight).order_by(Insight.created_at.desc()).limit(1)).scalar_one_or_none()
    if insight and insight.summary:
        parts.append("Latest analysis: " + insight.summary.strip())

    perf = db.execute(select(TopicPerformance).where(TopicPerformance.videos_count > 0)
                      .order_by(TopicPerformance.score.desc())).scalars().all()
    for kind, label in (("category", "Categories"), ("hook_style", "Hook styles"), ("keyword", "Keywords")):
        rows = [p for p in perf if p.kind == kind]
        if not rows:
            continue
        best = ", ".join(f"{p.key} (avg {p.avg_views:.0f} views, {p.avg_retention:.0f}% retention)" for p in rows[:4])
        parts.append(f"{label} that worked best: {best}")
        if len(rows) > 4:
            worst = ", ".join(p.key for p in rows[-2:])
            parts.append(f"{label} that underperformed: {worst}")

    winners = top_performers(db)
    if winners:
        lines = []
        for w in winners:
            ret = f", {w['retention']:.0f}% watched" if w["retention"] else ""
            lines.append(f"\"{w['title']}\" - {w['views']} views, {w['likes']} likes{ret}; category={w['category']}, "
                         f"hook style={w['hook_style']}, keywords={', '.join(w['keywords'][:4])}")
        parts.insert(0, "TOP PERFORMERS (our audience watches these to the end - make close relatives of them: same pace, hook type and story shape):\n- "
                     + "\n- ".join(lines))

    weak = weak_performers(db)
    if weak:
        parts.append("WEAK (viewers left early or ignored these - do not repeat their style): "
                     + "; ".join(f"\"{w['title']}\" ({w['views']} views"
                                 + (f", {w['retention']:.0f}% watched" if w["retention"] else "") + ")" for w in weak))

    if not parts:
        return "No performance data yet - explore broadly and vary topics, hooks and formats."
    return "\n".join(parts)[:max_chars]


def recent_topics(db: Session, limit: int = 80) -> list[str]:
    from ..models import Idea

    rows = db.execute(select(Idea.topic).order_by(Idea.created_at.desc()).limit(limit)).all()
    return [r[0] for r in rows]


def idea_boost(db: Session, category: str, keywords: list[str], hook_style: str) -> float:
    """-15..+25 points based on how similar past content performed."""
    perf = {(p.kind, p.key): p for p in db.execute(select(TopicPerformance)).scalars().all()}
    if not perf:
        return 0.0
    vals: list[float] = []
    for kind, key in [("category", category), ("hook_style", hook_style)] + [("keyword", k) for k in keywords or []]:
        p = perf.get((kind, normalize(key)))
        if p and p.videos_count > 0:
            vals.append(p.score)
    if not vals:
        return 0.0
    avg = sum(vals) / len(vals)  # 0..100, 50 = average
    return max(-15.0, min(25.0, (avg - 50.0) * 0.5))
