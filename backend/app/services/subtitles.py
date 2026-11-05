"""Caption building: word timings -> short on-screen chunks -> SRT + styled ASS (burned in by FFmpeg).

Pure functions (stdlib only) so they are unit-testable without FFmpeg.
"""
from __future__ import annotations

import re
from typing import Any

MAX_CHARS_PER_LINE = 16
MAX_WORDS_PER_CHUNK = 4
_PUNCT_BREAK = re.compile(r"[.!?…:;,—–-]$")


def estimate_word_timings(text: str, duration: float) -> list[dict[str, Any]]:
    """Fallback when the TTS gives no timings: spread words over the audio by character weight."""
    words = text.split()
    if not words or duration <= 0:
        return []
    weights = []
    for w in words:
        wt = len(w) + 1.0
        if re.search(r"[.!?…]$", w):
            wt += 4.0   # sentence pause
        elif re.search(r"[,;:—–]$", w):
            wt += 2.0
        weights.append(wt)
    total = sum(weights)
    out, t = [], 0.0
    for w, wt in zip(words, weights):
        d = duration * wt / total
        out.append({"text": w, "start": round(t, 3), "end": round(t + d, 3)})
        t += d
    return out


def chunk_words(words: list[dict[str, Any]], max_words: int = MAX_WORDS_PER_CHUNK,
                max_chars: int = MAX_CHARS_PER_LINE) -> list[list[dict[str, Any]]]:
    """Group words into readable 1-4 word captions, preferring breaks at punctuation."""
    chunks: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for w in words:
        text = str(w["text"]).strip()
        if not text:
            continue
        cur_len = sum(len(x["text"]) + 1 for x in cur) + len(text)
        if cur and (len(cur) >= max_words or cur_len > max_chars):
            chunks.append(cur)
            cur = []
        cur.append({**w, "text": text})
        if _PUNCT_BREAK.search(text) and len(cur) >= 2:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def _srt_time(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(chunks: list[list[dict[str, Any]]]) -> str:
    lines = []
    for i, ch in enumerate(chunks, 1):
        lines.append(f"{i}\n{_srt_time(ch[0]['start'])} --> {_srt_time(ch[-1]['end'])}\n"
                     + " ".join(w["text"] for w in ch) + "\n")
    return "\n".join(lines)


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


_PALETTES = {  # primary text, highlight (ASS &HBBGGRR&)
    "dark-blue": ("&H00FFFFFF", "&H0000E0FF"),
    "warm": ("&H00FFFFFF", "&H004CC9FF"),
    "neon": ("&H00FFFFFF", "&H00B4FF00"),
}


def to_ass(chunks: list[list[dict[str, Any]]], emphasis: list[dict[str, Any]] | None = None,
           width: int = 1080, height: int = 1920, font: str = "DejaVu Sans", style: str = "dark-blue") -> str:
    """Word-highlight captions (active word coloured) + optional emphasis texts per scene."""
    primary, highlight = _PALETTES.get(style, _PALETTES["dark-blue"])
    font_size = int(width * 0.078)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{font_size},{primary},{primary},&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,{int(height * 0.24)},1
Style: Emph,{font},{int(font_size * 1.15)},{highlight},{highlight},&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,8,3,8,60,60,{int(height * 0.12)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev: list[str] = []
    for ch in chunks:
        words = [_esc(w["text"]) for w in ch]
        for i, w in enumerate(ch):
            start = w["start"]
            end = ch[i + 1]["start"] if i + 1 < len(ch) else max(w["end"], w["start"] + 0.15)
            parts = []
            for j, txt in enumerate(words):
                if j == i:
                    parts.append(f"{{\\c{highlight}&\\fscx112\\fscy112}}{txt}{{\\c{primary}&\\fscx100\\fscy100}}")
                else:
                    parts.append(txt)
            ev.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{{\\fad(40,0)}}{' '.join(parts)}")
    for e in emphasis or []:
        txt = _esc(str(e.get("text", ""))).strip()
        if not txt:
            continue
        ev.append(f"Dialogue: 1,{_ass_time(e['start'])},{_ass_time(e['end'])},Emph,,0,0,0,,"
                  f"{{\\fad(200,200)\\t(0,250,\\fscx115\\fscy115)\\t(250,500,\\fscx100\\fscy100)}}{txt}")
    return header + "\n".join(ev) + "\n"
