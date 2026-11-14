"""AI-generated scene pictures (free, no API key): Pollinations.

Used when no licensed stock footage is available (or when visual_source = "ai"). Every scene gets its own
picture generated from the scene description; ffmpeg_utils.scene_from_image animates it (slow zoom / pan).
Network trouble never breaks a render: the caller falls back to the procedural background.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx

from ..events import get_logger

log = get_logger("images")

BASE = "https://image.pollinations.ai/prompt/"
# the anonymous tier is throttled to ~1 request / 15 s
MIN_GAP_S = 16.0
_SUFFIX = "vertical composition, no text, no letters, no watermark"
STYLE = f"cinematic illustration, ultra detailed, vivid colors, dramatic lighting, {_SUFFIX}"
# One look per video (chosen by video id): different videos look different, one video stays consistent.
VISUAL_STYLES = [
    STYLE,
    f"warm cozy storybook illustration, soft painted textures, gentle colors, {_SUFFIX}",
    f"3D claymation style, handmade plasticine look, soft studio light, {_SUFFIX}",
    f"vintage 35mm film photo look, fine grain, warm nostalgic colors, {_SUFFIX}",
    f"bold flat vector cartoon, thick outlines, bright playful palette, {_SUFFIX}",
    f"soft watercolor and ink illustration, expressive, light paper texture, {_SUFFIX}",
]
_last_call = 0.0


def pick_style(seed: int, landscape: bool = False) -> str:
    style = VISUAL_STYLES[abs(int(seed)) % len(VISUAL_STYLES)]
    return style.replace("vertical composition", "wide horizontal composition") if landscape else style


def build_prompt(visual_query: str, narration: str = "", topic: str = "", image_prompt: str = "",
                 style: str = "") -> str:
    style = style or STYLE
    ip = re.sub(r"\s+", " ", image_prompt or "").strip()
    if ip:  # a dedicated, scene-specific description written by the scriptwriter
        return f"{ip[:300]}, {style}"[:480]
    parts = [visual_query.strip()]
    if topic:
        parts.append(f"about {topic.strip()}")
    gist = re.sub(r"\s+", " ", narration or "").strip()[:110]
    if gist:
        parts.append(f"scene: {gist}")
    parts.append(style)
    return ", ".join(p for p in parts if p)[:480]


def _looks_like_image(data: bytes) -> bool:
    return len(data) > 8000 and (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
                                 or data[:4] == b"RIFF")


def generate(prompt: str, dest: Path, seed: int, *, attempts: int = 3, timeout: float = 100.0,
             landscape: bool = False) -> Optional[Path]:
    """Download one picture to `dest` (jpg). Returns None when the service is unavailable."""
    global _last_call
    url = f"{BASE}{quote(prompt, safe='')}"
    params = {"width": 1344 if landscape else 768, "height": 768 if landscape else 1344, "seed": seed % 1_000_000, "model": "flux", "nologo": "true"}
    for i in range(attempts):
        wait = MIN_GAP_S - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        try:
            r = httpx.get(url, params=params, timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": "YouTubeAIManager/1.0"})
            if r.status_code == 200 and _looks_like_image(r.content):
                dest.write_bytes(r.content)
                return dest
            log.warning("image attempt %d/%d: HTTP %s (%d bytes)", i + 1, attempts, r.status_code, len(r.content))
        except Exception as e:  # timeouts, resets, DNS
            log.warning("image attempt %d/%d failed: %s", i + 1, attempts, e)
        params["seed"] = (params["seed"] + 7919) % 1_000_000  # different seed on retry
    return None
