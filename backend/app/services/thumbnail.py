"""AI Thumbnail Creator: 1280x720 JPG from a real frame of the video + punchy headline, styled by mood."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageEnhance
from sqlalchemy.orm import Session

from ..events import log_event
from ..models import Video
from ..stages import PermanentError
from ..storage import get_storage
from . import ffmpeg_utils as ff

TW, TH = 1280, 720
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]
MOODS = {  # accent, text, overlay tint
    "shock": ((255, 59, 48), (255, 255, 255), (60, 0, 0)),
    "curious": ((255, 214, 10), (255, 255, 255), (10, 20, 60)),
    "calm": ((64, 224, 208), (255, 255, 255), (0, 30, 40)),
    "energetic": ((124, 252, 0), (255, 255, 255), (30, 0, 60)),
    "dark": ((255, 140, 0), (255, 255, 255), (0, 0, 0)),
}


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int) -> tuple[list[str], ImageFont.ImageFont]:
    words = text.upper().split()
    for size in range(150, 54, -6):
        font = _font(size)
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if draw.textlength(trial, font=font) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if len(lines) <= 3 and len(lines) * size * 1.12 <= max_h and all(
                draw.textlength(l, font=font) <= max_w for l in lines):
            return lines, font
    return [" ".join(words)[:24]], _font(60)


def compose(frame_path: str | Path, text: str, subtext: str, mood: str, out_path: str | Path) -> None:
    accent, fg, tint = MOODS.get(mood, MOODS["curious"])
    src = Image.open(frame_path).convert("RGB")
    # blurred, darkened full-bleed background from the same frame
    bg = src.resize((TW, int(TW * src.height / src.width)), Image.LANCZOS)
    top = max(0, (bg.height - TH) // 2)
    bg = bg.crop((0, top, TW, top + TH)).filter(ImageFilter.GaussianBlur(28))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    overlay = Image.new("RGB", (TW, TH), tint)
    bg = Image.blend(bg, overlay, 0.25)
    # sharp portrait frame on the right
    ph = TH - 60
    pw = int(ph * src.width / src.height)
    portrait = src.resize((pw, ph), Image.LANCZOS)
    px, py = TW - pw - 70, 30
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw, ph), radius=28, fill=255)
    glow = Image.new("RGB", (pw + 24, ph + 24), accent)
    gm = Image.new("L", glow.size, 0)
    ImageDraw.Draw(gm).rounded_rectangle((0, 0, glow.width, glow.height), radius=36, fill=255)
    bg.paste(glow, (px - 12, py - 12), gm)
    bg.paste(portrait, (px, py), mask)

    draw = ImageDraw.Draw(bg)
    left, max_w = 60, px - 60 - 50
    lines, font = _wrap(draw, text or "WATCH THIS", max_w, TH - 220)
    size = getattr(font, "size", 60)
    block_h = int(len(lines) * size * 1.12)
    y = max(50, (TH - block_h) // 2 - (30 if subtext else 0))
    draw.rectangle((left - 22, y - 8, left - 10, y + block_h + 4), fill=accent)
    for ln in lines:
        draw.text((left + 3, y + 5), ln, font=font, fill=(0, 0, 0))
        draw.text((left, y), ln, font=font, fill=fg, stroke_width=max(3, size // 22), stroke_fill=(0, 0, 0))
        y += int(size * 1.12)
    if subtext:
        sf = _font(max(36, size // 2))
        pad = 16
        tw = int(draw.textlength(subtext.upper(), font=sf))
        draw.rounded_rectangle((left, y + 14, left + tw + pad * 2, y + 14 + sf.size + pad), radius=14, fill=accent)
        draw.text((left + pad, y + 14 + pad // 2 - 2), subtext.upper(), font=sf, fill=(0, 0, 0))

    q = 92
    while True:
        bg.save(out_path, "JPEG", quality=q, optimize=True)
        if Path(out_path).stat().st_size <= 1_900_000 or q <= 60:  # YouTube limit is 2 MB
            break
        q -= 8


def make_thumbnail(db: Session, video: Video) -> None:
    if not video.video_key:
        raise PermanentError("no rendered video - cannot create thumbnail")
    storage = get_storage()
    work = Path(tempfile.mkdtemp(prefix=f"yam-thumb-{video.id}-"))
    try:
        frame = work / "frame.jpg"
        try:
            storage.get_file(f"videos/{video.id}/frame.jpg", frame)  # clean frame stored by the renderer
        except Exception:
            src = work / "video.mp4"
            storage.get_file(video.video_key, src)
            dur = video.duration_seconds or ff.probe(src)["duration"]
            ff.extract_frame(src, frame, at=min(max(0.9, dur * 0.18), max(0.5, dur - 1)))
        th = ((video.seo or {}).get("thumbnail") or {})
        text = th.get("text") or (video.script or {}).get("hook", "")[:22] or video.title[:22]
        out = work / "thumb.jpg"
        compose(frame, text, th.get("subtext", ""), th.get("mood", "curious"), out)
        key = f"videos/{video.id}/thumbnail.jpg"
        storage.put_file(out, key, "image/jpeg")
        video.thumbnail_key = key
        db.commit()
        log_event("thumbnail", f"thumbnail created: '{text}'", video_id=video.id)
    finally:
        shutil.rmtree(work, ignore_errors=True)
