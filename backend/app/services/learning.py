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

    pubs = db.execute(
        select(Publication).where(Publication.status == "published")
        .options(selectinload(Publication.snapshots)).order_by(Publication.published_at.desc()).limit(40)
    ).scalars().all()
    scored = []
    for p in pubs:
        if p.snapshots:
            s = max(p.snapshots, key=lambda x: x.fetched_at)
            scored.append((s.views, p.title))
    scored.sort(reverse=True)
    if scored:
        parts.append("Best titles so far: " + "; ".join(f"\"{t}\" ({v} views)" for v, t in scored[:3]))

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
