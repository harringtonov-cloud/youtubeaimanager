"""AI Scriptwriter: idea -> original scene-by-scene script (hook, voice-over, visual queries, captions)."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import log_event
from ..models import Video
from ..settings_store import AppSettings, get_settings
from ..stages import PermanentError
from . import learning, llm
from .text_utils import clamp, content_hash, jaccard, tokens

MIN_SCENES, MAX_SCENES = 4, 14


def _targets(s: AppSettings) -> tuple[int, int, int]:
    wps = learning.words_per_second(s.language)
    lo = int(s.min_video_seconds * wps * 0.92)
    hi = int(s.max_video_seconds * wps * 1.0)
    return lo, hi, (lo + hi) // 2


def _prompt(db: Session, video: Video, s: AppSettings, feedback: str = "") -> list[dict]:
    idea = video.idea
    lo, hi, mid = _targets(s)
    system = (
        "You are an award-winning YouTube Shorts scriptwriter. You write ORIGINAL scripts in a natural spoken voice: "
        "short sentences, concrete facts, zero filler, no clickbait lies, no unverifiable claims, no copying existing "
        f"videos or articles. Avoid entirely: {s.forbidden_topics}. Reply with JSON only."
    )
    user = f"""Write a script for a vertical YouTube Short.

Topic: {idea.topic if idea else video.title}
Angle: {idea.angle if idea else ''}
Suggested hook: {idea.hook if idea else ''}
Keywords: {', '.join(idea.keywords or []) if idea else ''}
Channel niche: {s.niche}
Audience: {s.audience}
Tone: {s.tone}
Language of voice-over and on-screen text: {learning.language_name(s.language)}

Length: the full voice-over must be between {lo} and {hi} words (aim for about {mid}).
HOOK RULES: the first sentence must be under 12 words, open a curiosity gap or state a startling specific fact
(a number, a contradiction, a "what if"), and never start with greetings, "Did you know" or "In this video".
Keep the pace fast: no scene longer than ~9 seconds of speech; end every beat on a mini-cliffhanger when possible.
Structure: 1) HOOK in the first 3 seconds - a surprising, specific statement or question that creates curiosity;
2) 3-6 tight content beats, each with a new fact or reveal; 3) a satisfying payoff or twist; 4) a punchy closing line that
echoes the hook so the video loops seamlessly. NO "subscribe/like/follow" begging.
PROVEN STYLE (viral Russian-language Shorts): a myth-busting or "how it really works" explainer about everyday things;
the hook challenges a common belief ("You think X? You are wrong.") or warns ("This job is not for ...");
documentary male narrator tone, simple words, one new fact every 3-4 seconds, a final line that reframes the hook.

What we learned from past videos (use it, do not mention it):
{learning.digest(db, 900)}
{('FIX THIS FROM THE PREVIOUS ATTEMPT: ' + feedback) if feedback else ''}

Return JSON:
{{"title": "working title, max 70 chars",
 "hook": "the exact first sentence",
 "scenes": [{{"voiceover": "what the narrator says in this scene (1-3 sentences)",
   "visual_query": "2-4 ENGLISH words to search licensed stock footage for this scene (concrete, filmable)",
   "image_prompt": "ENGLISH, 15-30 words: a vivid, concrete description of ONE cinematic still picture that illustrates this scene (subject, setting, lighting, camera angle); no text, no letters, no real people's names",
   "on_screen_text": "optional emphasis text, max 5 words, same language as voice-over, or empty"}}],
 "closing": "the last spoken line"}}
The scenes' voice-over together is the complete narration, in order, starting with the hook and ending with the
closing line. Use {MIN_SCENES}-{MAX_SCENES} scenes. PACE: one scene per sentence or half-sentence (about 2-3 seconds of speech each),
so a 35-second video has 10-14 scenes; each scene gets its own different picture.
Story format also works great: a short absurd/heartwarming "unbelievable but true-sounding" story with fast escalation
and a punchline ending."""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _words(text: str) -> int:
    return len(re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", text, flags=re.UNICODE))


def validate(data: Any, s: AppSettings) -> tuple[dict, str]:
    """Returns (clean_script, problem). `problem` is empty when the script is acceptable."""
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
        return {}, "answer must be a JSON object with a 'scenes' list"
    scenes = []
    for sc in data["scenes"]:
        if not isinstance(sc, dict):
            continue
        vo = re.sub(r"\s+", " ", str(sc.get("voiceover", ""))).strip()
        if not vo:
            continue
        q = re.sub(r"[^A-Za-z0-9 \-]", " ", str(sc.get("visual_query", ""))).strip()
        scenes.append({
            "voiceover": vo,
            "visual_query": re.sub(r"\s+", " ", q)[:60] or "abstract background",
            "on_screen_text": clamp(str(sc.get("on_screen_text", "")), 40),
            "image_prompt": re.sub(r"[^A-Za-z0-9 ,.\-]", " ", str(sc.get("image_prompt", ""))).strip()[:300],
        })
    if len(scenes) < MIN_SCENES:
        return {}, f"too few scenes ({len(scenes)}); use {MIN_SCENES}-{MAX_SCENES}"
    scenes = scenes[:MAX_SCENES]
    voiceover = " ".join(sc["voiceover"] for sc in scenes)
    n = _words(voiceover)
    lo, hi, _ = _targets(s)
    problem = ""
    if n < lo * 0.85:
        problem = f"voice-over is too short ({n} words); it must have {lo}-{hi} words"
    elif n > hi * 1.15:
        problem = f"voice-over is too long ({n} words); it must have {lo}-{hi} words - cut it down"
    script = {
        "title": clamp(str(data.get("title", "")), 90),
        "hook": clamp(str(data.get("hook") or scenes[0]["voiceover"]), 300),
        "closing": clamp(str(data.get("closing") or scenes[-1]["voiceover"]), 300),
        "scenes": scenes,
        "voiceover": voiceover,
        "word_count": n,
        "language": s.language,
    }
    return script, problem


def write_script(db: Session, video: Video) -> None:
    s = get_settings(db)
    if not llm.is_configured():
        raise PermanentError("LLM_API_KEY is not configured")

    others = db.execute(select(Video.id, Video.script).where(Video.script.is_not(None), Video.id != video.id)
                        .order_by(Video.id.desc()).limit(60)).all()
    other_tokens = [(vid, tokens(sc.get("voiceover", ""))) for vid, sc in others if isinstance(sc, dict)]

    feedback = ""
    script: dict = {}
    for attempt in range(1, 4):
        data = llm.chat_json(_prompt(db, video, s, feedback), temperature=0.85, max_tokens=2500)
        script, problem = validate(data, s)
        if not script:
            feedback = problem
            continue
        h = content_hash(script["title"], script["voiceover"])
        clash = db.execute(select(Video.id).where(Video.content_hash == h, Video.id != video.id)).first()
        mine = tokens(script["voiceover"])
        similar = next((vid for vid, t in other_tokens if jaccard(mine, t) >= 0.75), None)
        if clash or similar:
            problem = "the script is too similar to an existing video - take a clearly different angle and wording"
        if not problem:
            video.script = script
            video.content_hash = h
            if script["title"] and not video.title:
                video.title = script["title"]
            db.commit()
            log_event("scriptwriter", f"script ready ({script['word_count']} words, {len(script['scenes'])} scenes)",
                      video_id=video.id)
            return
        feedback = problem
        log_event("scriptwriter", f"script attempt {attempt} rejected: {problem}", level="WARNING", video_id=video.id)

    # accept a slightly-off-length script rather than looping forever, but never a duplicate
    if script and "similar" not in feedback:
        lo, hi, _ = _targets(s)
        if lo * 0.6 <= script["word_count"] <= hi * 1.4:
            h = content_hash(script["title"], script["voiceover"])
            if not db.execute(select(Video.id).where(Video.content_hash == h, Video.id != video.id)).first():
                video.script, video.content_hash = script, h
                db.commit()
                log_event("scriptwriter", "script accepted with relaxed length", level="WARNING", video_id=video.id)
                return
    raise llm.TransientLLMError(f"could not produce a valid script: {feedback}")
