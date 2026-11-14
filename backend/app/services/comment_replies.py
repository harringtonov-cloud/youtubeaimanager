"""Friendly AI replies to viewers' comments under our recent Shorts (more comments -> more reach).

Free: one cheap LLM call per sweep (batch), YouTube quota is tiny (list = 1 unit, reply = 50 units, a few a day).

Safety rules (a public channel must never say something stupid):
  * only top-level comments of other people that nobody has answered yet (no reply -> no duplicate, no state needed)
  * the model may refuse (empty reply) for spam, insults, politics, personal data, anything unclear - we then stay silent
  * links, hashtags, @mentions and "subscribe" begging are stripped / rejected
  * every handled comment id is remembered, so a refused or failed comment is not retried every sweep
  * hard daily cap (`auto_reply_per_day`), at most PER_SWEEP replies per run, a pause between replies
  * a sweep runs at most every 15 minutes and holds a short lock, so API cron + runner never reply twice
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import log_event
from ..models import Publication, utcnow
from ..settings_store import get_flag, get_settings, set_flag
from ..stages import PermanentError
from . import learning, llm, youtube
from .youtube import QuotaExceeded, YouTubeError

INTERVAL = timedelta(minutes=15)
LOCK_TTL = timedelta(minutes=8)
RECENT = timedelta(days=3)
MAX_VIDEOS = 6
PER_VIDEO = 3
PER_SWEEP = 3
PAUSE = 4.0          # seconds between two replies
MAX_SEEN = 400
MAX_COMMENT_CHARS = 400
MAX_REPLY_CHARS = 200

F_LAST, F_LOCK, F_DAY, F_SEEN = "reply_last_run", "reply_lock", "reply_day", "reply_seen"

_LINKISH = re.compile(r"(https?://|www\.|t\.me/|@\w|\.(com|ru|io|me|org|net)\b)", re.IGNORECASE)
_BEGGING = re.compile(r"(подпис(ывай|ы|ыв)|subscribe|лайк(ни|айте)|follow me)", re.IGNORECASE)


def _clean_reply(text: str, original: str) -> str:
    t = re.sub(r"[<>#]|https?://\S+|www\.\S+", "", str(text or ""))
    t = re.sub(r"\s+", " ", t).strip().strip('"«»')
    if not t or len(re.findall(r"\w", t)) < 3:
        return ""
    if _LINKISH.search(t) or _BEGGING.search(t):
        return ""
    if t.lower() == " ".join(original.split()).lower():
        return ""
    return t[:MAX_REPLY_CHARS].rstrip()


def _ok_to_answer(text: str) -> bool:
    t = " ".join(text.split())
    return 2 <= len(re.findall(r"\w", t)) and len(t) <= MAX_COMMENT_CHARS and not _LINKISH.search(t)


def _today() -> str:
    return utcnow().strftime("%Y-%m-%d")


def _used_today(db: Session) -> int:
    d = get_flag(db, F_DAY) or {}
    return int(d.get("n", 0)) if d.get("day") == _today() else 0


def _bump_today(db: Session, by: int) -> None:
    set_flag(db, F_DAY, {"day": _today(), "n": _used_today(db) + by})


def _seen(db: Session) -> list[str]:
    v = get_flag(db, F_SEEN)
    return [str(x) for x in v] if isinstance(v, list) else []


def _remember(db: Session, ids: list[str]) -> None:
    if ids:
        set_flag(db, F_SEEN, (_seen(db) + ids)[-MAX_SEEN:])


def _lock_free(db: Session) -> bool:
    raw = get_flag(db, F_LOCK)
    try:
        return not raw or utcnow() - datetime.fromisoformat(str(raw)) > LOCK_TTL
    except Exception:
        return True


def _due(db: Session) -> bool:
    raw = get_flag(db, F_LAST)
    try:
        return not raw or utcnow() - datetime.fromisoformat(str(raw)) >= INTERVAL
    except Exception:
        return True


def find_new_comments(db: Session, own_channel_id: str, budget: int) -> list[dict[str, Any]]:
    """Unanswered comments of other people under the latest published videos, newest videos first."""
    now = utcnow()
    pubs = db.execute(
        select(Publication).where(Publication.status == "published", Publication.youtube_video_id.is_not(None),
                                  Publication.published_at > now - RECENT)
        .order_by(Publication.published_at.desc()).limit(MAX_VIDEOS)
    ).scalars().all()
    seen = set(_seen(db))
    out: list[dict[str, Any]] = []
    for p in pubs:
        if len(out) >= budget:
            break
        try:
            threads = youtube.list_comment_threads(db, p.youtube_video_id)
        except QuotaExceeded:
            raise
        except YouTubeError as e:  # comments disabled / video not ready: skip this video
            log_event("replies", f"cannot read comments of {p.youtube_video_id}: {e}", level="WARNING")
            continue
        taken = 0
        for th in threads:
            sn = th.get("snippet") or {}
            top = sn.get("topLevelComment") or {}
            body = top.get("snippet") or {}
            cid = str(top.get("id") or "")
            author = (body.get("authorChannelId") or {}).get("value")
            text = str(body.get("textOriginal") or body.get("textDisplay") or "").strip()
            if (not cid or cid in seen or author == own_channel_id or int(sn.get("totalReplyCount") or 0) > 0
                    or sn.get("canReply") is False):
                continue
            if not _ok_to_answer(text):
                _remember(db, [cid])  # emoji only / link / too long: never answer, never look at it again
                continue
            out.append({"id": cid, "video": p.title or "", "text": text,
                        "author": str(body.get("authorDisplayName") or "")[:40]})
            taken += 1
            if taken >= PER_VIDEO or len(out) >= budget:
                break
    return out


def draft_replies(db: Session, items: list[dict[str, Any]]) -> dict[str, str]:
    """{comment_id: reply}; ids with no (or an unacceptable) reply are left out on purpose."""
    s = get_settings(db)
    listing = "\n".join(f'{i + 1}. id={it["id"]} | video: "{it["video"][:80]}" | comment: "{it["text"][:300]}"'
                        for i, it in enumerate(items))
    system = ("You run the YouTube channel of short, funny, warm stories. You reply to viewers' comments briefly, "
              "kindly and naturally, like a real channel owner. Reply with JSON only.")
    user = f"""Write replies to these comments under our Shorts.

Channel niche: {s.niche}
Language: the same language as each comment (default: {learning.language_name(s.language)})

Comments:
{listing}

Rules for every reply:
- max 140 characters, one or two short sentences, warm, a little humour, 0-1 emoji
- answer what the person actually said; if it fits, end with a short question that invites another reply
- our stories are invented comedy - never claim that events really happened or give facts you are not sure of
- no links, no hashtags, no @mentions, no "subscribe/like" requests, no promises, no personal data
- if the comment is spam, rude, political, about adult topics, asks for personal data, is unclear, or you cannot
  answer honestly in a friendly way - use an EMPTY reply "" (staying silent is fine)

Return JSON: {{"replies": [{{"id": "<id from the list>", "reply": "<text or empty>"}}]}}"""
    data = llm.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}],
                         temperature=0.7, max_tokens=700)
    rows = data.get("replies") if isinstance(data, dict) else data
    by_id = {it["id"]: it["text"] for it in items}
    out: dict[str, str] = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("id") or "")
        if cid in by_id:
            reply = _clean_reply(r.get("reply", ""), by_id[cid])
            if reply:
                out[cid] = reply
    return out


def process(db: Session) -> dict[str, int]:
    """One sweep. Safe to call on every tick: it throttles itself (every 15 min) and never raises."""
    res: dict[str, int] = {}
    s = get_settings(db)
    if not s.auto_reply_enabled or not llm.is_configured() or not youtube.is_connected(db):
        return res
    if youtube.quota_blocked(db) or not youtube.has_scope(db, youtube.COMMENT_SCOPE):
        return res  # a missing scope is reported by the first-comment job (with the reconnect hint)
    if not _due(db) or not _lock_free(db):
        return res
    budget = min(PER_SWEEP, s.auto_reply_per_day - _used_today(db))
    set_flag(db, F_LAST, utcnow().isoformat())
    if budget <= 0:
        return {"daily_cap": 1}
    set_flag(db, F_LOCK, utcnow().isoformat())
    try:
        acct = youtube.get_account(db)
        items = find_new_comments(db, acct.channel_id, budget) if acct else []
        if not items:
            return res
        replies = draft_replies(db, items)
        _remember(db, [it["id"] for it in items if it["id"] not in replies])  # declined: do not ask again
        sent = 0
        for it in items:
            text = replies.get(it["id"])
            if not text:
                res["declined"] = res.get("declined", 0) + 1
                continue
            try:
                youtube.reply_to_comment(db, it["id"], text)
            except QuotaExceeded as e:
                log_event("replies", f"reply postponed (quota): {e}", level="WARNING")
                res["quota"] = 1
                break
            except YouTubeError as e:
                _remember(db, [it["id"]])
                res["failed"] = res.get("failed", 0) + 1
                level = "ERROR" if youtube.classify_comment_error(e) == "reauth" else "WARNING"
                log_event("replies", f"reply not posted: {e}", level=level)
                if youtube.classify_comment_error(e) == "reauth":
                    break
                continue
            _remember(db, [it["id"]])
            sent += 1
            res["replied"] = res.get("replied", 0) + 1
            log_event("replies", f"replied to a comment: {text}", comment=it["text"][:120])
            if sent < len(items):
                time.sleep(PAUSE)
        if sent:
            _bump_today(db, sent)
    except PermanentError as e:
        log_event("replies", f"replies waiting: {e}", level="WARNING")
        res["waiting"] = 1
    except Exception as e:  # LLM hiccup, network ...: try again in the next sweep, never break the caller
        log_event("replies", f"reply sweep failed: {type(e).__name__}: {e}", level="WARNING")
        res["error"] = 1
    finally:
        set_flag(db, F_LOCK, "")
    return res
