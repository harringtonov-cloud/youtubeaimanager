"""Text helpers used for de-duplication (ideas, scripts) and safe formatting. Stdlib only."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable

_STOP = {
    # en
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "was", "were", "be", "why",
    "how", "what", "when", "who", "does", "do", "did", "this", "that", "these", "those", "with", "your",
    "you", "it", "its", "at", "by", "from", "as", "can", "will", "about", "into", "than", "then", "there",
    # ru
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то", "все", "она", "так", "его",
    "но", "да", "ты", "к", "у", "же", "вы", "за", "бы", "по", "только", "ее", "мне", "было", "вот", "от",
    "меня", "еще", "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну", "вдруг", "ли", "если", "уже",
    "или", "ни", "быть", "был", "него", "до", "вас", "нибудь", "опять", "уж", "вам", "ведь", "там", "потом",
    "себя", "ничего", "ей", "может", "они", "тут", "где", "есть", "надо", "ней", "для", "мы", "тебя", "их",
    "чем", "была", "сам", "чтоб", "без", "будто", "чего", "раз", "тоже", "себе", "под", "будет", "ж",
    "тогда", "кто", "этот", "того", "потому", "этого", "какой", "совсем", "ним", "здесь", "этом", "один",
    "почти", "мой", "тем", "чтобы", "нее", "сейчас", "были", "куда", "зачем", "всех", "никогда", "можно",
    "при", "наконец", "два", "об", "другой", "хоть", "после", "над", "больше", "тот", "через", "эти",
    "нас", "про", "всего", "них", "какая", "много", "разве", "три", "эту", "моя", "впрочем", "хорошо",
    "свою", "этой", "перед", "иногда", "лучше", "чуть", "том", "нельзя", "такой", "им", "более", "всегда",
    "почему", "это",
}

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower().replace("ё", "е")
    return " ".join(_WORD_RE.findall(text))


def tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if t not in _STOP and len(t) > 1}


def fingerprint(topic: str) -> str:
    """Order- and filler-insensitive fingerprint: 'Why is the sky blue' == 'sky blue, why?'."""
    toks = sorted(tokens(topic)) or [normalize(topic)]
    return hashlib.sha256(" ".join(toks).encode()).hexdigest()


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_similar(topic: str, existing: Iterable[str], threshold: float = 0.6) -> bool:
    t = tokens(topic)
    if not t:
        return False
    for other in existing:
        if jaccard(t, tokens(other)) >= threshold:
            return True
    return False


def content_hash(title: str, voiceover: str) -> str:
    return hashlib.sha256((normalize(title) + "|" + normalize(voiceover)).encode()).hexdigest()


def clamp(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    sp = cut.rfind(" ")
    if sp > limit * 0.6:
        cut = cut[:sp]
    return cut.rstrip(" ,;:-—") + "…"


def clean_tag(tag: str) -> str:
    tag = re.sub(r"[#<>]", "", tag or "").strip()
    return re.sub(r"\s+", " ", tag)


def to_hashtag(tag: str) -> str:
    words = _WORD_RE.findall(unicodedata.normalize("NFKC", tag or ""))
    return "#" + "".join(w.capitalize() for w in words) if words else ""
