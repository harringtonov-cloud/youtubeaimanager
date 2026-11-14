"""AI Video Creator: script -> voice-over -> licensed footage per scene -> FFmpeg montage -> 1080x1920 MP4."""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..events import log_event
from ..models import Video
from ..settings_store import get_settings
from ..stages import PermanentError, heartbeat
from ..storage import get_storage
from . import ffmpeg_utils as ff
from . import images, stock, subtitles, tts

FADE = 0.35
TAIL = 0.45  # seconds of picture after the last word


def scene_durations(scene_texts: list[str], words: list[dict[str, Any]], total: float) -> list[float]:
    """Split the audio timeline into scenes. Boundaries snap to real word timings when available."""
    n = len(scene_texts)
    counts = [max(1, len(t.split())) for t in scene_texts]
    total_words = sum(counts)
    bounds: list[float] = [0.0]
    cum = 0
    for i in range(n - 1):
        cum += counts[i]
        if words:
            idx = min(len(words) - 1, max(0, round(cum / total_words * len(words))))
            t = words[idx]["start"]
        else:
            t = total * cum / total_words
        bounds.append(t)
    bounds.append(total)
    durs = [max(1.2, bounds[i + 1] - bounds[i]) for i in range(n)]
    # keep the sum exactly equal to `total` after enforcing the minimum length
    scale = total / sum(durs)
    return [d * scale for d in durs]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) > 2}


def emphasis_events(scenes: list[dict], durs: list[float]) -> list[dict[str, Any]]:
    """Big on-screen text per scene. Skipped when it only repeats what the captions already say."""
    out, t = [], 0.0
    for sc, d in zip(scenes, durs):
        txt = (sc.get("on_screen_text") or "").strip()
        ew = _words(txt)
        if txt and ew and len(ew & _words(sc.get("voiceover", ""))) / len(ew) >= 0.6:
            txt = ""  # duplicate of the spoken / captioned words
        if txt:
            out.append({"text": txt, "start": t + 0.15, "end": t + min(d, 2.6)})
        t += d
    return out


SFX_LEAD = 0.10        # the whoosh starts slightly before the cut so its swell peaks on it
SFX_MIN_GAP = 0.8      # never more often than this, even if the picture changes faster


def cut_times(durs: list[float], shots: dict[int, tuple[int, float]], total: float) -> list[float]:
    """Moments (seconds in the final video) where the picture changes, as whoosh start times.

    * scene i begins at sum(durs[:i]) - that is where the crossfade into it starts
    * `shots` maps a scene index to (shots, clip length) for scenes cut into several shots; the hard cuts
      between them are spread evenly over the clip."""
    cuts: list[float] = []
    start = 0.0
    for i, d in enumerate(durs):
        if i > 0:
            cuts.append(start)
        n, length = shots.get(i, (1, d))
        for k in range(1, n):
            cuts.append(start + k * length / n)
        start += d
    out: list[float] = []
    for t in sorted(cuts):
        t = max(0.0, t - SFX_LEAD)
        if t > total - 0.5 or (out and t - out[-1] < SFX_MIN_GAP):
            continue
        out.append(round(t, 2))
    return out


def render_video(db: Session, video: Video) -> None:
    if not video.script:
        raise PermanentError("script missing - cannot render")
    s = get_settings(db)
    script = video.script
    scenes: list[dict] = script["scenes"]
    voiceover = script.get("voiceover") or " ".join(sc["voiceover"] for sc in scenes)
    storage = get_storage()
    work = Path(tempfile.mkdtemp(prefix=f"yam-render-{video.id}-"))
    try:
        # 1) voice-over (whole text at once for natural prosody) + word timings
        voice = tts.synthesize(voiceover, s.language, work / "voice.mp3", provider=s.tts_provider, voice=s.tts_voice)
        total = voice["duration"]
        if total > 178:
            raise PermanentError(f"voice-over is {total:.0f}s long - too long for a Short")
        heartbeat(db, video)

        # 2) timeline
        durs = scene_durations([sc["voiceover"] for sc in scenes], voice["words"], total)
        durs[-1] += TAIL
        total_video = sum(durs)

        # 3) footage per scene (licensed stock, otherwise generated background)
        used: set[str] = set()
        ai_ok, ai_used, ai_fail = True, 0, 0
        clips_meta: list[dict] = []
        scene_files: list[Path] = []
        shots: dict[int, tuple[int, float]] = {}  # scene -> (shots, clip length) for hard cuts inside a scene
        for i, (sc, d) in enumerate(zip(scenes, durs)):
            length = d + (FADE if i < len(scenes) - 1 else 0.0)
            dst = work / f"scene{i:02d}.mp4"
            clip = None
            if s.visual_source in ("auto", "pexels", "pixabay"):
                clip = stock.find_clip(sc.get("visual_query") or "abstract background", length, work, s.visual_source,
                                       used)
            if clip:
                used.add(clip.clip_id)
                try:
                    ff.scene_from_clip(clip.path, dst, length)
                    clips_meta.append({"scene": i, **clip.meta()})
                except ff.FFmpegError as e:  # corrupt download -> generated fallback for this scene
                    log_event("video", f"stock clip {clip.clip_id} unusable, using generated background: {e}",
                              level="WARNING", video_id=video.id)
                    clip = None
            if not clip and s.visual_source in ("auto", "ai") and ai_ok:
                prompt = images.build_prompt(sc.get("visual_query") or "", sc.get("voiceover", ""),
                                             str(script.get("title", "")), sc.get("image_prompt") or "",
                                             style=images.pick_style(video.id))
                pic = images.generate(prompt, work / f"pic{i:02d}.jpg", seed=video.id * 100 + i)
                if pic:
                    try:
                        ff.scene_from_image_shots(pic, dst, length, variant=i)
                        shots[i] = (ff.shot_count(length), length)
                        clips_meta.append({"scene": i, "provider": "pollinations",
                                           "license": "AI-generated image (Pollinations, flux)", "prompt": prompt[:300]})
                        ai_used += 1
                        clip = True  # type: ignore[assignment]
                    except ff.FFmpegError as e:
                        log_event("video", f"AI picture unusable, using generated background: {e}",
                                  level="WARNING", video_id=video.id)
                    pic.unlink(missing_ok=True)
                else:
                    ai_fail += 1
                    if ai_fail >= 2 and ai_used == 0:  # service is down: stop waiting for it in this render
                        ai_ok = False
                        log_event("video", "AI picture service unavailable, using generated backgrounds",
                                  level="WARNING", video_id=video.id)
            if not clip:
                ff.scene_generated(dst, length, s.background_style, i + video.id)
                clips_meta.append({"scene": i, "provider": "generated", "license": "original (procedurally generated)"})
            scene_files.append(dst)
            if clip and clip is not True:
                Path(clip.path).unlink(missing_ok=True)
            heartbeat(db, video)

        # 4) montage with transitions
        base = work / "base.mp4"
        ff.concat_with_transitions(scene_files, durs, base, fade=FADE, seed=video.id)
        heartbeat(db, video)

        # clean frame (no captions) for the thumbnail
        frame = work / "frame.jpg"
        ff.extract_frame(base, frame, at=min(max(0.9, total_video * 0.18), max(0.5, total_video - 1.0)))
        fkey = f"videos/{video.id}/frame.jpg"
        storage.put_file(frame, fkey, "image/jpeg")

        # 5) captions + emphasis -> ASS (burned in) and SRT (kept for the dashboard / YouTube captions)
        chunks = subtitles.chunk_words(voice["words"])
        ass = work / "captions.ass"
        ass.write_text(subtitles.to_ass(chunks, emphasis_events(scenes, durs), style=s.background_style), "utf-8")
        srt = work / "captions.srt"
        srt.write_text(subtitles.to_srt(chunks), "utf-8")

        # 6) final mix
        out = work / "final.mp4"
        music = None
        if s.music_enabled:
            try:
                music = work / "music.wav"
                ff.make_music(music, total_video, video.id)
            except Exception as e:  # music is optional
                music = None
                log_event("video", f"background music skipped: {e}", level="WARNING", video_id=video.id)
        sfx = None
        if s.sfx_enabled:
            try:  # "вжух" on every picture change; optional, never blocks the render
                whoosh = work / "whoosh.wav"
                ff.make_whoosh(whoosh)
                track = work / "sfx.wav"
                if ff.make_sfx_track(track, whoosh, cut_times(durs, shots, total_video), total_video):
                    sfx = track
            except Exception as e:
                sfx = None
                log_event("video", f"sound effects skipped: {e}", level="WARNING", video_id=video.id)
        try:
            ff.final_mix(base, voice["path"], ass, out, total_video, music=music, music_volume=s.music_volume,
                         sfx=sfx, sfx_volume=s.sfx_volume)
        except ff.FFmpegError as e:
            if not music and not sfx:
                raise
            log_event("video", f"mix with music/effects failed, rendering voice-only: {e}", level="WARNING",
                      video_id=video.id)
            ff.final_mix(base, voice["path"], ass, out, total_video)
        info = ff.probe(out)
        if not (info["has_video"] and info["has_audio"]) or info["width"] != ff.W or info["height"] != ff.H:
            raise RuntimeError(f"render verification failed: {info}")
        if not (5 <= info["duration"] <= 179):
            raise RuntimeError(f"unexpected duration {info['duration']:.1f}s")

        # 7) store artifacts (key only is kept in the DB -> restart safe)
        vkey, skey = f"videos/{video.id}/video.mp4", f"videos/{video.id}/captions.srt"
        storage.put_file(out, vkey, "video/mp4")
        storage.put_file(srt, skey, "application/x-subrip")
        video.video_key, video.subtitle_key = vkey, skey
        video.duration_seconds = round(info["duration"], 2)
        video.assets = {
            "clips": clips_meta,
            "tts": {"provider": voice["provider"], "voice": voice["voice"]},
            "note": "Pictures are licensed stock (Pexels/Pixabay), AI-generated (Pollinations) or procedurally generated; voice-over is TTS.",
        }
        db.commit()
        log_event("video", f"rendered {info['duration']:.1f}s video ({len(scenes)} scenes)", video_id=video.id)
    finally:
        shutil.rmtree(work, ignore_errors=True)
