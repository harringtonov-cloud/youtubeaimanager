"""AI Director: finds ideas, keeps the content calendar full, never repeats itself.

Idea pipeline:  trend signals + performance digest + recent topics  ->  LLM  ->  validation
(fingerprint + similarity de-duplication, forbidden topics)  ->  scored `Idea` rows.
Planning:       every free publishing slot in the horizon gets the best unused idea.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..events import log_event
from ..models import CANCELLED, Idea, Video, utcnow
from ..scheduling import next_slots
from ..settings_store import AppSettings, get_flag, get_settings, set_flag
from . import learning, llm, trends
from .text_utils import clamp, clean_tag, fingerprint, is_similar, normalize

IDEAS_PER_RUN = 10
MIN_IDEA_POOL = 4            # generate more ideas when fewer unused ones remain
IDEA_RETRY_MINUTES = 45      # don't hammer the LLM if generation keeps failing
SLOT_TOLERANCE = timedelta(minutes=20)

HOOK_STYLES = ["question", "shocking-fact", "myth-busting", "countdown", "story", "comparison", "challenge", "secret"]


def _system_prompt(s: AppSettings) -> str:
    return (
        "You are the creative director of a YouTube Shorts channel. You invent ORIGINAL, factual, engaging short-form "
        "video ideas that make people stop scrolling. You never copy existing videos, never invent facts, and avoid "
        f"these topics entirely: {s.forbidden_topics}. Reply with JSON only."
    )


def _user_prompt(s: AppSettings, signals: list[dict], digest: str, recent: list[str], n: int) -> str:
    exploit = max(0, round(n * (1 - s.explore_ratio)))
    if "TOP PERFORMERS" in digest:  # we have winners: lean into them
        exploit = max(exploit, round(n * 0.7))
    explore = n - exploit
    sig = "\n".join(f"- {x['title']} ({x['source']}{', ' + x['signal'] if x.get('signal') else ''})"
                    for x in signals[:30]) or "- (no trend data available)"
    rec = "\n".join(f"- {t}" for t in recent[:60]) or "- (none yet)"
    return f"""Channel niche: {s.niche}
Audience: {s.audience}
Tone: {s.tone}
Language of the channel: {learning.language_name(s.language)}
Video length: {s.min_video_seconds}-{s.max_video_seconds} seconds, vertical Shorts.

WHAT WE LEARNED FROM PAST VIDEOS:
{digest}

CURRENT TREND SIGNALS (inspiration only - do not copy titles, connect them to the niche when it fits):
{sig}

TOPICS ALREADY COVERED OR PLANNED (never repeat or paraphrase these):
{rec}

Top-performing format on similar channels (1-4M views per Short): a ~36-second absurd, heartwarming "official
bureaucracy meets everyday life" story. Titles are short, past tense, and combine an ordinary person/animal/object with an
official or unexpected outcome (e.g. "A cat was officially enrolled in the rescue service", "The janitor flooded a perfect
rink with a garden hose", "A snowman was registered by traffic police"). Stories are clearly fictional comedy: invented
characters, no real people, brands or claims of real news.
Another idea style that works for us: myth-busting and "hidden mechanics of everyday things" (why buildings fall, how a
dangerous job is really done, what happens inside familiar objects); every idea needs a hook that challenges a belief.
Create {n} new video ideas: {exploit} that build on what worked and {explore} that explore fresh themes within the niche.
If TOP PERFORMERS are listed above, at least half of the "build on what worked" ideas must be close relatives of them:
same category and hook style, the same kind of payoff, but a clearly different specific subject (a "part 2", the next
surprising fact, the opposite case, a related thing the audience will ask about). Never repeat or paraphrase a covered topic. Every idea must be a specific, surprising, verifiable topic - not a vague theme.

Return JSON: {{"ideas": [{{"topic": "specific topic, max 100 chars", "angle": "the unique angle / payoff in one sentence",
"hook": "first spoken sentence (max 15 words)", "keywords": ["3-6 search keywords, lowercase"],
"category": "one or two word content category, lowercase", "hook_style": "one of {', '.join(HOOK_STYLES)}",
"trend_signal": "the trend this connects to, or empty", "potential": 1-10}}]}}"""


def _forbidden_hit(text: str, forbidden: str) -> bool:
    t = normalize(text)
    for word in (w.strip() for w in forbidden.replace("\n", ",").split(",")):
        w = normalize(word)
        # only check single, specific words; multi-word phrases are enforced through the prompt
        if w and " " not in w and len(w) > 4 and w in t.split():
            return True
    return False


def generate_ideas(db: Session, n: int = IDEAS_PER_RUN) -> int:
    """Research + create new ideas. Returns how many were stored."""
    if not llm.is_configured():
        log_event("director", "idea generation skipped: LLM_API_KEY is not configured", level="WARNING")
        return 0
    s = get_settings(db)
    signals = trends.collect_signals(db, s.trend_regions)
    digest = learning.digest(db)
    recent = learning.recent_topics(db)
    data = llm.chat_json(
        [{"role": "system", "content": _system_prompt(s)}, {"role": "user", "content": _user_prompt(s, signals, digest, recent, n)}],
        temperature=0.95, max_tokens=3500,
    )
    raw = data.get("ideas") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise llm.TransientLLMError("Director answer has no 'ideas' list")

    existing = recent + [t for (t,) in db.execute(select(Idea.topic)).all()][:500]
    existing_fp = {fp for (fp,) in db.execute(select(Idea.fingerprint)).all()}
    created = 0
    for it in raw:
        if not isinstance(it, dict):
            continue
        topic = clamp(str(it.get("topic", "")), 120)
        if len(topic) < 8:
            continue
        if _forbidden_hit(topic + " " + str(it.get("angle", "")), s.forbidden_topics):
            continue
        fp = fingerprint(topic)
        if fp in existing_fp or is_similar(topic, existing, 0.6):
            continue
        kws = [normalize(clean_tag(str(k))) for k in (it.get("keywords") or []) if str(k).strip()][:8]
        category = normalize(str(it.get("category", "")))[:80]
        style = str(it.get("hook_style", "")).strip().lower()
        style = style if style in HOOK_STYLES else ""
        try:
            potential = max(1.0, min(10.0, float(it.get("potential", 5))))
        except (TypeError, ValueError):
            potential = 5.0
        signal = str(it.get("trend_signal", "")).strip()[:300]
        score = 40.0 + potential * 3.5 + (8.0 if signal else 0.0) + learning.idea_boost(db, category, kws, style)
        idea = Idea(topic=topic, angle=clamp(str(it.get("angle", "")), 600), hook=clamp(str(it.get("hook", "")), 300),
                    keywords=kws, category=category, hook_style=style, source="trend" if signal else "director",
                    trend_signal=signal, score=round(score, 2), status="new", fingerprint=fp)
        db.add(idea)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        existing.append(topic)
        existing_fp.add(fp)
        created += 1
    log_event("director", f"Director created {created} new ideas ({len(raw)} proposed)", signals=len(signals))
    return created


# --------------------------------------------------------------------------- planning
def _free_idea(db: Session) -> Optional[Idea]:
    """Owner-approved ideas first, then the highest score."""
    approved = db.execute(
        select(Idea).where(Idea.status == "approved").order_by(Idea.score.desc(), Idea.created_at).limit(1)
    ).scalar_one_or_none()
    if approved:
        return approved
    return db.execute(
        select(Idea).where(Idea.status == "new").order_by(Idea.score.desc(), Idea.created_at).limit(1)
    ).scalar_one_or_none()


def _pool_size(db: Session) -> int:
    return len(db.execute(select(Idea.id).where(Idea.status.in_(["new", "approved"]))).all())


def _taken_times(db: Session, horizon_end: datetime) -> list[datetime]:
    rows = db.execute(
        select(Video.scheduled_at).where(Video.scheduled_at.is_not(None), Video.status != CANCELLED,
                                         Video.scheduled_at < horizon_end + timedelta(days=1),
                                         Video.scheduled_at > utcnow() - timedelta(hours=1))
    ).all()
    return [r[0] for r in rows if r[0]]


def _slot_is_taken(slot: datetime, taken: list[datetime]) -> bool:
    return any(abs(slot - t) <= SLOT_TOLERANCE for t in taken)


def create_video_for_idea(db: Session, idea: Idea, scheduled_at: Optional[datetime] = None) -> Video:
    s = get_settings(db)
    if scheduled_at is None:
        now = utcnow()
        taken = _taken_times(db, now + timedelta(days=30))
        for slot in next_slots(now, 30, s.publish_times, s.timezone, min_lead_minutes=45):
            if not _slot_is_taken(slot, taken):
                scheduled_at = slot
                break
    video = Video(idea_id=idea.id, title=idea.topic[:300], scheduled_at=scheduled_at)
    idea.status = "used"
    idea.used_at = utcnow()
    db.add(video)
    db.commit()  # Video.idea_id is UNIQUE -> a concurrent planner cannot create a second video for the idea
    log_event("director", f"planned video for '{idea.topic}'", video_id=video.id,
              scheduled_at=scheduled_at.isoformat() if scheduled_at else None)
    return video


def plan_content(db: Session) -> list[int]:
    """Fill every free slot in the planning horizon. Returns ids of newly created videos."""
    if not llm.is_configured():
        return []
    s = get_settings(db)
    now = utcnow()
    slots = next_slots(now, s.plan_horizon_days, s.publish_times, s.timezone, min_lead_minutes=45)
    taken = _taken_times(db, now + timedelta(days=s.plan_horizon_days))
    free = [sl for sl in slots if not _slot_is_taken(sl, taken)]
    if not free:
        return []

    if _pool_size(db) < max(MIN_IDEA_POOL, len(free)):
        last = get_flag(db, "last_idea_attempt")
        recent_try = False
        if last:
            try:
                recent_try = datetime.fromisoformat(last) > now - timedelta(minutes=IDEA_RETRY_MINUTES)
            except ValueError:
                pass
        if not recent_try:
            set_flag(db, "last_idea_attempt", now.isoformat())
            try:
                generate_ideas(db)
            except Exception as e:  # LLM hiccup must not stop planning with the ideas we already have
                log_event("director", f"idea generation failed: {e}", level="WARNING")

    created: list[int] = []
    for slot in free[:4]:
        idea = _free_idea(db)
        if idea is None:
            break
        try:
            video = create_video_for_idea(db, idea, slot)
        except IntegrityError:
            db.rollback()
            continue
        created.append(video.id)
    return created
