"""AI SEO Manager: title, description, hashtags, tags, keywords, category (+ thumbnail text)."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import log_event
from ..models import Video
from ..settings_store import AppSettings, get_settings, language_for
from ..stages import PermanentError
from . import learning, llm
from .text_utils import clamp, clean_tag, to_hashtag

# YouTube video categories valid for uploads
CATEGORIES = {
    "1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music", "15": "Pets & Animals", "17": "Sports",
    "19": "Travel & Events", "20": "Gaming", "22": "People & Blogs", "23": "Comedy", "24": "Entertainment",
    "26": "Howto & Style", "27": "Education", "28": "Science & Technology",
}
TITLE_MAX = 70


def _prompt(db: Session, video: Video, s: AppSettings) -> list[dict]:
    sc = video.script or {}
    idea = video.idea
    long = video.format == "long"
    kind = "long-form video (a full story of several minutes)" if long else "Short"
    cats = ", ".join(f"{k}={v}" for k, v in CATEGORIES.items())
    system = (f"You are a YouTube SEO specialist for {'long videos' if long else 'Shorts'}. You write accurate, curiosity-driven metadata that matches "
              "the video's real content (no misleading claims). Reply with JSON only.")
    user = f"""Create YouTube metadata for this {kind}.

Language: {learning.language_name(language_for(s, video))}
Niche: {s.niche}
Topic: {idea.topic if idea else video.title}
Keywords from research: {', '.join(idea.keywords or []) if idea else ''}
Hook: {sc.get('hook', '')}
Full narration: {sc.get('voiceover', '')}

What worked before (match the style, never copy):
{learning.digest(db, 700)}

Rules:
- title: max {TITLE_MAX} characters, front-load the main keyword, create curiosity, no ALL CAPS, no emoji spam (0-1 emoji).
- description: {'3-5 short paragraphs (max 1500 characters): what the story is about without spoiling the twist' if long else '2-4 short paragraphs (max 900 characters)'}; the first sentence repeats the main keyword and the promise,
  then context, then a soft call to subscribe. Do not include hashtags in it.
- hashtags: {'3-4, no spaces, WITHOUT the # sign (do NOT use "Shorts")' if long else '3-5, no spaces, WITHOUT the # sign; always include "Shorts" as the last one'}.
- tags: 8-14 search phrases people actually type (1-3 words each).
- keywords: 1 primary keyword + 4 secondary keywords.
- category_id: pick ONE id from: {cats}
- thumbnail: {{"text": "2-4 words, punchy, max 22 characters, in the video language",
  "subtext": "optional 1-3 words or empty", "mood": "one of: shock, curious, calm, energetic, dark"}}

Return JSON: {{"title": "", "description": "", "hashtags": [], "tags": [], "keywords": {{"primary": "", "secondary": []}},
"category_id": "", "thumbnail": {{"text": "", "subtext": "", "mood": ""}},
"comment": ""}}

comment: one short pinned-style first comment (max 140 characters, in the video language). It must be a question that
takes one word to answer: a clear A-or-B choice taken from the video ("Would you sign it or run: yes or no?"), or a
"which would you pick: X or Y?". Specific to this video, playful, answerable without thinking. No links, no hashtags,
no "like/subscribe" begging."""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def sanitize(data: dict, s: AppSettings, fallback_title: str, long: bool = False) -> dict:
    title = re.sub(r"\s+", " ", re.sub(r"[<>]", "", str(data.get("title") or fallback_title))).strip()
    title = clamp(title, TITLE_MAX) or clamp(fallback_title, TITLE_MAX)

    tags_raw = [clean_tag(str(t)) for t in (data.get("tags") or [])]
    tags, total = [], 0
    for t in tags_raw:
        if not t or len(t) > 40 or t.lower() in {x.lower() for x in tags}:
            continue
        if total + len(t) + 1 > 473:  # YouTube allows 500 chars in total; keep room for the 'shorts' tag
            break
        tags.append(t)
        total += len(t) + 1
    if long:
        tags = [t for t in tags if t.lower() != "shorts"]
    elif "shorts" not in {t.lower() for t in tags}:
        tags.append("shorts")

    hashtags: list[str] = []
    for h in data.get("hashtags") or []:
        tag = to_hashtag(str(h).lstrip("#"))
        if tag and tag.lower() not in {x.lower() for x in hashtags}:
            hashtags.append(tag)
    hashtags = [h for h in hashtags if h.lower() != "#shorts"][:4] + ([] if long else ["#Shorts"])

    desc = re.sub(r"[<>]", "", str(data.get("description", ""))).strip()
    desc = re.sub(r"#\w+", "", desc)
    desc = re.sub(r"\n{3,}", "\n\n", desc).strip()
    description = clamp(desc, 1500 if long else 900) + "\n\n" + " ".join(hashtags)

    kw = data.get("keywords") or {}
    keywords = {"primary": clamp(str(kw.get("primary", "")), 60) if isinstance(kw, dict) else "",
                "secondary": [clamp(str(x), 40) for x in (kw.get("secondary") or [])][:6] if isinstance(kw, dict) else []}

    cat = str(data.get("category_id", "")).strip()
    category_id = cat if cat in CATEGORIES else (s.category_id if s.category_id in CATEGORIES else "27")

    comment = re.sub(r"\s+", " ", re.sub(r"[<>#]|https?://\S+", "", str(data.get("comment", "")))).strip()[:150]

    th = data.get("thumbnail") if isinstance(data.get("thumbnail"), dict) else {}
    mood = str(th.get("mood", "")).lower()
    thumbnail = {
        "text": clamp(str(th.get("text", "")), 24),
        "subtext": clamp(str(th.get("subtext", "")), 24),
        "mood": mood if mood in ("shock", "curious", "calm", "energetic", "dark") else "curious",
    }
    return {
        "title": title, "description": description, "hashtags": hashtags, "tags": tags, "keywords": keywords,
        "category_id": category_id, "category_name": CATEGORIES[category_id], "thumbnail": thumbnail,
        "comment": comment,
    }


def _with_short_link(db: Session, video: Video, description: str, lang: str = "en") -> str:
    """A long video points to the Short it expands (cross-promotion), once the Short is on YouTube."""
    from ..models import Publication

    if not video.parent_video_id:
        return description
    pub = db.execute(select(Publication).where(Publication.video_id == video.parent_video_id,
                                               Publication.youtube_video_id.is_not(None))).scalar_one_or_none()
    if pub is None:
        return description
    head, _, tags = description.rpartition("\n\n")
    label = "Короткая версия" if lang.lower().startswith("ru") else "Short version"
    return f"{head}\n\n{label}: https://youtube.com/shorts/{pub.youtube_video_id}\n\n{tags}".strip()


def generate_seo(db: Session, video: Video) -> None:
    if not llm.is_configured():
        raise PermanentError("LLM_API_KEY is not configured")
    if not video.script:
        raise PermanentError("script missing - cannot create SEO")
    s = get_settings(db)
    data = llm.chat_json(_prompt(db, video, s), temperature=0.6, max_tokens=1800)
    if not isinstance(data, dict):
        raise llm.TransientLLMError("SEO answer is not a JSON object")
    long = video.format == "long"
    seo = sanitize(data, s, video.title or (video.idea.topic if video.idea else "Short"), long=long)
    if long:
        seo["description"] = _with_short_link(db, video, seo["description"], language_for(s, video))
    if not seo["thumbnail"]["text"]:
        seo["thumbnail"]["text"] = clamp(seo["title"], 22)
    video.seo = seo
    video.title = seo["title"]
    db.commit()
    log_event("seo", f"SEO ready: '{seo['title']}' ({len(seo['tags'])} tags)", video_id=video.id)
