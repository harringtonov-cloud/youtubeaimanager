"""Script for a LONG (16:9, several minutes) video: the full version of a story already told in a Short.

Built in two steps so a small, cheap model keeps the plot together:
  1. an outline of N chapters (title + what happens) from the Short's story
  2. one call per chapter that writes its scenes (each scene = one picture, about 11 seconds of speech)
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import log_event
from ..models import Idea, Video
from ..settings_store import AppSettings, get_settings
from ..stages import PermanentError
from . import learning, llm
from .text_utils import clamp, content_hash

SCENE_WORDS = 30           # about 11-13 seconds of speech per picture
MIN_SHARE = 0.7            # accept the script when it has at least this share of the target length


def plan(minutes: int, language: str) -> dict[str, int]:
    """Target size of the script for a video of `minutes`."""
    words = int(minutes * 60 * learning.words_per_second(language))
    chapters = max(4, min(10, round(minutes * 1.2)))
    return {"words": words, "chapters": chapters, "chapter_words": words // chapters,
            "scenes_per_chapter": max(2, round(words / chapters / SCENE_WORDS))}


def clean_scenes(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for sc in raw if isinstance(raw, list) else []:
        if not isinstance(sc, dict):
            continue
        vo = re.sub(r"\s+", " ", str(sc.get("voiceover", ""))).strip()
        if not vo:
            continue
        q = re.sub(r"[^A-Za-z0-9 \-]", " ", str(sc.get("visual_query", ""))).strip()
        out.append({
            "voiceover": vo,
            "visual_query": re.sub(r"\s+", " ", q)[:60] or "abstract background",
            "on_screen_text": clamp(str(sc.get("on_screen_text", "")), 40),
            "image_prompt": re.sub(r"[^A-Za-z0-9 ,.\-]", " ", str(sc.get("image_prompt", ""))).strip()[:300],
        })
    return out


def count_words(text: str) -> int:
    return len(re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", text, flags=re.UNICODE))


def _system(s: AppSettings) -> str:
    return ("You are an award-winning storyteller who writes ORIGINAL, warm, funny spoken narration for YouTube videos. "
            "Short sentences, concrete details, natural spoken language, no filler, no unverifiable claims. "
            f"Avoid entirely: {s.forbidden_topics}. Reply with JSON only.")


def _source_texts(parent: dict) -> dict[str, str]:
    """Wording that depends on whether the long video expands a published Short or starts from a bare idea."""
    if parent.get("from_short", True):
        return {"intro": "We published a Short with this story. Now we make the FULL {minutes}-minute version of the same story.",
                "label": "Short's narration", "title": "Short's title",
                "plan": "Keep the same characters, the same absurd premise and the same ending idea, but develop it: "
                        "more characters, funny details, dialogue, escalation, an unexpected twist, a warm finale.",
                "genre": "It is clearly fictional comedy: invented characters only. Chapter 1 opens with the hook of the Short.",
                "chapter_source": "The Short we already published (the same story, short version)",
                "chapter_genre": "Fictional comedy, invented characters, no real people."}
    return {"intro": "Make a {minutes}-minute video about this idea (nothing was published about it yet).",
            "label": "Idea (angle and hook)", "title": "Idea",
            "plan": "Develop it into a complete story or explainer: a strong opening, escalation, concrete details, "
                    "an unexpected turn and a satisfying finale.",
            "genre": "If the idea is an absurd story, it is clearly fictional comedy with invented characters. If it is a "
                     "myth-busting or 'how it really works' topic, make an engaging explainer with accurate, well-known "
                     "facts and never invent statistics, studies or claims. Chapter 1 opens with the hook.",
            "chapter_source": "The idea this video is about",
            "chapter_genre": "Absurd stories: fictional comedy with invented characters. Explainers: accurate facts only. "
                             "No real people."}


def _outline(db: Session, s: AppSettings, parent: dict, n: int, minutes: int) -> dict:
    t = _source_texts(parent)
    user = f"""{t['intro'].format(minutes=minutes)}

Language of everything you write: {learning.language_name(s.long_video_language)}
Channel niche: {s.niche}
{t['title']}: {parent.get('title', '')}
{t['label']}: {parent.get('voiceover', '')}

Plan the long video as exactly {n} chapters. {t['plan']}
{t['genre']}

Return JSON: {{"title": "video title, max 70 chars, same style as the Short's title",
 "chapters": [{{"title": "chapter name, 2-5 words", "summary": "2 sentences: what happens in this chapter"}}]}}"""
    return llm.chat_json([{"role": "system", "content": _system(s)}, {"role": "user", "content": user}],
                         temperature=0.8, max_tokens=1200)


def _chapter(db: Session, s: AppSettings, parent: dict, outline: list[dict], idx: int, previous: str,
             target: dict[str, int], feedback: str) -> list[dict[str, str]]:
    t = _source_texts(parent)
    n = len(outline)
    plan_text = "\n".join(f"{i + 1}. {c['title']}: {c['summary']}" for i, c in enumerate(outline))
    cur = outline[idx]
    first, last = idx == 0, idx == n - 1
    rules = ("Open with a hook: the first sentence is under 10 words and starts in the middle of the action."
             if first else
             "Continue the story seamlessly from the previous chapter; do not recap and do not greet.")
    rules += (" End the video with a warm funny finale and one short question to the viewers (what would you do?). "
              "No begging to subscribe or like." if last else
              " End with a small cliffhanger that makes people want the next chapter.")
    user = f"""Write chapter {idx + 1} of {n} of a long story video.

Language: {learning.language_name(s.long_video_language)} (voice-over AND on-screen text)
Whole story plan:
{plan_text}

{t['chapter_source']}: {parent.get('voiceover', '')[:900]}

This chapter: "{cur['title']}" - {cur['summary']}
The previous chapter ended with: {previous or '(this is the first chapter)'}

{rules}
Length: about {target['chapter_words']} words of narration in total, split into about {target['scenes_per_chapter']} scenes of
roughly {SCENE_WORDS} words (2-3 sentences) each. {t['chapter_genre']}
{('FIX THIS FROM THE PREVIOUS ATTEMPT: ' + feedback) if feedback else ''}

Return JSON: {{"scenes": [{{"voiceover": "what the narrator says in this scene",
 "visual_query": "2-4 ENGLISH words for stock footage search",
 "image_prompt": "ENGLISH, 15-30 words: one vivid cinematic still picture for this scene (subject, setting, lighting, camera angle), no text, no real people's names",
 "on_screen_text": "optional emphasis, max 4 words, same language as the voice-over, or empty"}}]}}"""
    data = llm.chat_json([{"role": "system", "content": _system(s)}, {"role": "user", "content": user}],
                         temperature=0.85, max_tokens=2200)
    return clean_scenes(data.get("scenes") if isinstance(data, dict) else data)


def write_long_script(db: Session, video: Video) -> None:
    s = get_settings(db)
    if not llm.is_configured():
        raise PermanentError("LLM_API_KEY is not configured")
    parent_video = db.get(Video, video.parent_video_id) if video.parent_video_id else None
    parent = dict((parent_video.script or {}) if parent_video else {})
    parent["from_short"] = True
    if not parent.get("voiceover"):
        idea = db.get(Idea, video.source_idea_id) if video.source_idea_id else None
        if idea is None:
            raise PermanentError("this long video has neither a Short to expand nor an idea to start from")
        parent = {"title": idea.topic, "voiceover": " ".join(x for x in (idea.hook, idea.angle) if x),
                  "from_short": False}
    target = plan(s.long_video_minutes, s.long_video_language)

    outline_data = _outline(db, s, parent, target["chapters"], s.long_video_minutes)
    raw = outline_data.get("chapters") if isinstance(outline_data, dict) else None
    outline = [{"title": clamp(str(c.get("title", "")), 60) or f"Part {i + 1}",
                "summary": clamp(str(c.get("summary", "")), 300)}
               for i, c in enumerate(raw if isinstance(raw, list) else []) if isinstance(c, dict)]
    if len(outline) < 3:
        raise llm.TransientLLMError("outline of the long video is too short")
    outline = outline[:target["chapters"]]

    scenes: list[dict[str, str]] = []
    chapter_marks: list[dict[str, Any]] = []
    previous = ""
    for idx in range(len(outline)):
        got: list[dict[str, str]] = []
        feedback = ""
        for _attempt in range(2):
            got = _chapter(db, s, parent, outline, idx, previous, target, feedback)
            words = sum(count_words(x["voiceover"]) for x in got)
            if got and words >= target["chapter_words"] * 0.6:
                break
            feedback = (f"the chapter had only {words} words; it must have about {target['chapter_words']} words "
                        f"in about {target['scenes_per_chapter']} scenes - develop it with details and dialogue")
        if not got:
            raise llm.TransientLLMError(f"chapter {idx + 1} of the long video came back empty")
        chapter_marks.append({"title": outline[idx]["title"], "scene": len(scenes)})
        scenes.extend(got)
        previous = " ".join(got[-1]["voiceover"].split()[-40:])

    voiceover = " ".join(x["voiceover"] for x in scenes)
    n_words = count_words(voiceover)
    if n_words < target["words"] * MIN_SHARE:
        raise llm.TransientLLMError(f"long script is too short ({n_words} of ~{target['words']} words)")

    title = clamp(str((outline_data or {}).get("title", "")) if isinstance(outline_data, dict) else "", 90) \
        or video.title
    script = {"title": title, "hook": scenes[0]["voiceover"][:300], "closing": scenes[-1]["voiceover"][:300],
              "scenes": scenes, "voiceover": voiceover, "word_count": n_words, "language": s.long_video_language,
              "format": "long", "chapters": chapter_marks}
    h = content_hash(script["title"], voiceover)
    if db.execute(select(Video.id).where(Video.content_hash == h, Video.id != video.id)).first():
        raise llm.TransientLLMError("long script duplicates an existing video")
    video.script, video.content_hash = script, h
    if title:
        video.title = title
    db.commit()
    log_event("scriptwriter", f"long script ready ({n_words} words, {len(scenes)} scenes, {len(outline)} chapters)",
              video_id=video.id)


def estimated_minutes(words: int, language: str) -> float:
    return round(words / learning.words_per_second(language) / 60, 1) if words else 0.0


__all__ = ["plan", "write_long_script", "clean_scenes", "count_words", "estimated_minutes"]
