"""Caption building: word timings -> short on-screen chunks -> SRT + styled ASS (burned in by FFmpeg).

Pure functions (stdlib only) so they are unit-testable without FFmpeg.
"""
from __future__ import annotations

import re
from typing import Any

MAX_CHARS_PER_LINE = 20
MAX_WORDS_PER_CHUNK = 4
_PUNCT_BREAK = re.compile(r"[.!?…:;,—–-]$")
_SENTENCE_END = re.compile(r"[.!?…][\"')\]]*$")


def _norm(w: str) -> str:
    return re.sub(r"[^\w]+", "", w.lower())


def attach_punctuation(words: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """TTS word boundaries come without punctuation: copy the script's tokens back (with their . , ! ?).

    Needed so captions can break at sentence ends. Words that cannot be matched keep their own text.
    """
    tokens = text.split()
    ni, out = 0, []
    for w in words:
        nw = _norm(str(w["text"]))
        hit = None
        for j in range(ni, min(len(tokens), ni + 4)):
            if nw and _norm(tokens[j]) == nw:
                hit = j
                break
        if hit is not None:
            out.append({**w, "text": tokens[hit]})
            ni = hit + 1
        else:
            out.append(w)
    return out


def estimate_word_timings(text: str, duration: float, start: float = 0.0) -> list[dict[str, Any]]:
    """Fallback when the TTS gives no timings: spread words over [start, start+duration] by character weight.

    Pass the speech window (leading/trailing silence removed) for a much closer fit.
    """
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
    out, t = [], float(start)
    for w, wt in zip(words, weights):
        d = duration * wt / total
        out.append({"text": w, "start": round(t, 3), "end": round(t + d, 3)})
        t += d
    return out


def timings_from_sentences(sentences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sentence boundaries from the TTS ({text,start,end}) -> word timings, estimated inside each sentence.

    Errors cannot accumulate across the script: every sentence is re-anchored to its real position.
    """
    out: list[dict[str, Any]] = []
    for sen in sentences:
        dur = float(sen["end"]) - float(sen["start"])
        out.extend(estimate_word_timings(str(sen["text"]), dur, float(sen["start"])))
    return out


def _split_balanced(seg: list[dict[str, Any]], max_words: int, max_chars: int) -> list[list[dict[str, Any]]]:
    """Cut one sentence into captions of similar size (no lonely last word)."""
    if not seg:
        return []
    k = -(-len(seg) // max_words)                      # ceil
    while True:
        size = -(-len(seg) // k)
        parts = [seg[i:i + size] for i in range(0, len(seg), size)]
        too_long = any(sum(len(w["text"]) + 1 for w in p) - 1 > max_chars for p in parts)
        if not too_long or k >= len(seg):
            return parts
        k += 1


def chunk_words(words: list[dict[str, Any]], max_words: int = MAX_WORDS_PER_CHUNK,
                max_chars: int = MAX_CHARS_PER_LINE) -> list[list[dict[str, Any]]]:
    """Group words into readable 1-4 word captions. Sentences never share a caption."""
    clean = [{**w, "text": str(w["text"]).strip()} for w in words if str(w["text"]).strip()]
    chunks: list[list[dict[str, Any]]] = []
    seg: list[dict[str, Any]] = []
    for w in clean:
        seg.append(w)
        if _SENTENCE_END.search(w["text"]):
            chunks.extend(_split_balanced(seg, max_words, max_chars))
            seg = []
    chunks.extend(_split_balanced(seg, max_words, max_chars))
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
    wide = width > height  # 16:9: smaller text sitting lower, so it does not cover the picture
    font_size = int(height * 0.062) if wide else int(width * 0.078)
    cap_margin = int(height * 0.09) if wide else int(height * 0.24)
    emph_margin = int(height * 0.08) if wide else int(height * 0.12)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{font_size},{primary},{primary},&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,{4 if wide else 7},2,2,60,60,{cap_margin},1
Style: Emph,{font},{int(font_size * 1.15)},{highlight},{highlight},&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,{5 if wide else 8},3,8,60,60,{emph_margin},1

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
