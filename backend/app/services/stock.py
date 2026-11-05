"""Licensed stock footage: Pexels and Pixabay (free licences allow commercial use, no attribution required).

We store provider / id / author / page URL / licence per clip in `video.assets` for auditability.
Nothing is ever taken from YouTube or other sites.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import httpx

from ..config import get_config
from ..events import get_logger

log = get_logger("stock")

MAX_BYTES = 80 * 1024 * 1024
LICENSES = {"pexels": "Pexels License", "pixabay": "Pixabay Content License"}


@dataclass
class Clip:
    path: str
    provider: str
    clip_id: str
    page_url: str
    author: str
    license: str
    duration: float

    def meta(self) -> dict:
        d = asdict(self)
        d.pop("path")
        return d


def _download(url: str, dest: Path) -> None:
    with httpx.stream("GET", url, timeout=httpx.Timeout(120, connect=20), follow_redirects=True) as r:
        r.raise_for_status()
        size = 0
        with dest.open("wb") as fh:
            for chunk in r.iter_bytes(1 << 16):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise RuntimeError("stock clip too large")
                fh.write(chunk)


def _pexels(query: str, min_dur: float, exclude: set[str]) -> Optional[tuple[str, dict]]:
    key = get_config().pexels_api_key
    if not key:
        return None
    r = httpx.get("https://api.pexels.com/videos/search", headers={"Authorization": key},
                  params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 15}, timeout=20)
    r.raise_for_status()
    for v in r.json().get("videos", []):
        vid = f"pexels:{v['id']}"
        if vid in exclude or v.get("duration", 0) < min(min_dur, 4):
            continue
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
        portrait = [f for f in files if f["height"] >= f["width"]] or files
        if not portrait:
            continue
        # prefer ~1080 wide (sharp enough, small download)
        best = min(portrait, key=lambda f: (abs(f["width"] - 1080), f["width"] > 2200))
        return best["link"], {"clip_id": vid, "page_url": v.get("url", ""), "author": (v.get("user") or {}).get("name", ""),
                              "provider": "pexels", "duration": float(v.get("duration", 0))}
    return None


def _pixabay(query: str, min_dur: float, exclude: set[str]) -> Optional[tuple[str, dict]]:
    key = get_config().pixabay_api_key
    if not key:
        return None
    r = httpx.get("https://pixabay.com/api/videos/", params={"key": key, "q": query, "per_page": 20,
                                                              "safesearch": "true"}, timeout=20)
    r.raise_for_status()
    for h in r.json().get("hits", []):
        vid = f"pixabay:{h['id']}"
        if vid in exclude or h.get("duration", 0) < min(min_dur, 4):
            continue
        vs = h.get("videos", {})
        pick = vs.get("large") or vs.get("medium") or vs.get("small")
        if not pick or not pick.get("url"):
            continue
        return pick["url"], {"clip_id": vid, "page_url": h.get("pageURL", ""), "author": h.get("user", ""),
                             "provider": "pixabay", "duration": float(h.get("duration", 0))}
    return None


def find_clip(query: str, min_duration: float, dest_dir: Path, source: str = "auto",
              exclude: Optional[set[str]] = None) -> Optional[Clip]:
    """Best licensed clip for `query`, downloaded into dest_dir. None if nothing usable (-> generated background)."""
    exclude = exclude or set()
    order = {"auto": [_pexels, _pixabay], "pexels": [_pexels], "pixabay": [_pixabay], "generated": []}.get(source, [])
    queries = [query] + ([" ".join(query.split()[:2])] if len(query.split()) > 2 else [])
    for q in queries:
        for fn in order:
            try:
                found = fn(q, min_duration, exclude)
            except Exception as e:
                log.warning("%s search failed for '%s': %s", fn.__name__, q, e)
                continue
            if not found:
                continue
            url, meta = found
            dest = dest_dir / (meta["clip_id"].replace(":", "_") + ".mp4")
            try:
                _download(url, dest)
            except Exception as e:
                log.warning("download failed for %s: %s", meta["clip_id"], e)
                continue
            return Clip(path=str(dest), provider=meta["provider"], clip_id=meta["clip_id"], page_url=meta["page_url"],
                        author=meta["author"], license=LICENSES[meta["provider"]], duration=meta["duration"])
    return None
