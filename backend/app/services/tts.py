"""Voice-over synthesis with word timings (for captions).

edge   - Microsoft Edge neural voices via `edge-tts` (free, no key; unofficial endpoint, so we retry and can
         fall back to an OpenAI-compatible endpoint when a key is available)
openai - any OpenAI-compatible /audio/speech endpoint (timings are estimated from the audio length)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

import httpx

from ..config import get_config
from ..events import get_logger
from ..stages import PermanentError
from . import ffmpeg_utils as ff
from .subtitles import attach_punctuation, estimate_word_timings, timings_from_sentences

log = get_logger("tts")

EDGE_VOICES = {
    "en": "en-US-AndrewMultilingualNeural", "ru": "ru-RU-DmitryNeural", "uz": "uz-UZ-SardorNeural",
    "es": "es-ES-AlvaroNeural", "de": "de-DE-ConradNeural", "fr": "fr-FR-HenriNeural",
    "pt": "pt-BR-AntonioNeural", "it": "it-IT-DiegoNeural", "tr": "tr-TR-AhmetNeural",
    "ar": "ar-SA-HamedNeural", "hi": "hi-IN-MadhurNeural", "id": "id-ID-ArdiNeural",
    "ja": "ja-JP-KeitaNeural", "ko": "ko-KR-InJoonNeural", "zh": "zh-CN-YunxiNeural",
    "uk": "uk-UA-OstapNeural", "pl": "pl-PL-MarekNeural", "kk": "kk-KZ-DauletNeural",
}
EDGE_RATE = "+6%"


def default_voice(language: str, provider: str) -> str:
    if provider == "openai":
        return get_config().tts_voice or "alloy"
    return EDGE_VOICES.get((language or "en").lower().split("-")[0], EDGE_VOICES["en"])


async def _edge(text: str, voice: str, out: Path, sentences: Optional[list] = None) -> list[dict[str, Any]]:
    import edge_tts

    try:
        comm = edge_tts.Communicate(text, voice, rate=EDGE_RATE, boundary="WordBoundary")
    except TypeError:  # edge-tts 6.x: word boundaries are the default
        comm = edge_tts.Communicate(text, voice, rate=EDGE_RATE)
    words: list[dict[str, Any]] = []
    with out.open("wb") as fh:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append({"text": chunk["text"], "start": start, "end": start + chunk["duration"] / 1e7})
            elif chunk["type"] == "SentenceBoundary" and sentences is not None:
                start = chunk["offset"] / 1e7
                sentences.append({"text": chunk["text"], "start": start, "end": start + chunk["duration"] / 1e7})
    return words


def _synth_edge(text: str, voice: str, out: Path, sentences: Optional[list] = None) -> list[dict[str, Any]]:
    last: Optional[Exception] = None
    for attempt in range(3):
        try:
            if sentences is not None:
                sentences.clear()
            words = asyncio.run(asyncio.wait_for(_edge(text, voice, out, sentences), timeout=120))
            if out.exists() and out.stat().st_size > 1000:
                return words
            last = RuntimeError("edge-tts returned no audio")
        except Exception as e:
            last = e
        log.warning("edge-tts attempt %d failed: %s", attempt + 1, last)
        import time

        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Edge TTS failed: {last}")


def _synth_openai(text: str, voice: str, out: Path) -> list[dict[str, Any]]:
    c = get_config()
    base = (c.tts_base_url or c.llm_base_url).rstrip("/")
    key = c.tts_api_key or c.llm_api_key
    if not key:
        raise PermanentError("TTS_API_KEY (or LLM_API_KEY) is required for TTS_PROVIDER=openai")
    r = httpx.post(f"{base}/audio/speech", headers={"Authorization": f"Bearer {key}"},
                   json={"model": c.tts_model, "input": text[:4000], "voice": voice, "response_format": "mp3"},
                   timeout=180)
    if r.status_code in (401, 403):
        raise PermanentError(f"TTS authentication failed ({r.status_code})")
    if r.status_code >= 400:
        raise RuntimeError(f"TTS HTTP {r.status_code}: {r.text[:200]}")
    out.write_bytes(r.content)
    return []


def synthesize(text: str, language: str, out_path: str | Path, provider: Optional[str] = None,
               voice: str = "") -> dict[str, Any]:
    """Returns {path, duration, words, provider, voice}. Word timings are always present (estimated if needed)."""
    c = get_config()
    out = Path(out_path)
    prov = provider or c.tts_provider or "edge"
    v = voice or default_voice(language, prov)
    words: list[dict[str, Any]] = []
    sentences: list[dict[str, Any]] = []
    used = prov
    if prov == "edge":
        try:
            words = _synth_edge(text, v, out, sentences)
        except Exception as e:
            if c.tts_api_key or (c.llm_api_key and "openai.com" in (c.tts_base_url or c.llm_base_url)):
                log.warning("falling back to OpenAI-compatible TTS: %s", e)
                used, v = "openai", default_voice(language, "openai")
                words = _synth_openai(text, v, out)
            else:
                raise
    else:
        words = _synth_openai(text, v, out)
    duration = ff.audio_duration(out)
    timing = "word"
    if len(words) < max(3, len(text.split()) // 3):  # no word-level timings from the TTS
        n_words = len(text.split())
        if sentences and sum(len(str(x["text"]).split()) for x in sentences) >= n_words * 0.8:
            words, timing = timings_from_sentences(sentences), "sentence"
        else:
            start, end = ff.speech_window(out, duration)
            words, timing = estimate_word_timings(text, end - start, start), "estimate"
    if timing == "word":
        words = attach_punctuation(words, text)
    log.info("voice-over %.1fs, caption timings: %s (%d words)", duration, timing, len(words))
    return {"path": str(out), "duration": duration, "words": words, "provider": used, "voice": v,
            "timing": timing}
