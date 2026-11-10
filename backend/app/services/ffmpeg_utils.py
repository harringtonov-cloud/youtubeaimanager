"""Thin, well-tested wrappers around ffmpeg/ffprobe. No database, no network."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional

W, H, FPS = 1080, 1920, 30
TRANSITIONS = ["fade", "slideleft", "wipeup", "circleopen", "smoothright", "fadeblack", "slideup", "radial"]


class FFmpegError(RuntimeError):
    pass


def run(cmd: list[str], timeout: int = 900) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(f"{cmd[0]} timed out after {timeout}s") from e
    except FileNotFoundError as e:
        raise FFmpegError(f"{cmd[0]} is not installed") from e
    if p.returncode != 0:
        tail = "\n".join(p.stderr.strip().splitlines()[-12:])
        raise FFmpegError(f"{cmd[0]} failed (exit {p.returncode}): {tail}")
    return p.stdout


def probe(path: str | Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], 60)
    data = json.loads(out)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = float(data.get("format", {}).get("duration") or 0)
    return {
        "duration": dur,
        "width": int(v["width"]) if v else None,
        "height": int(v["height"]) if v else None,
        "has_video": v is not None,
        "has_audio": a is not None,
        "vcodec": v.get("codec_name") if v else None,
        "acodec": a.get("codec_name") if a else None,
    }


def audio_duration(path: str | Path) -> float:
    d = probe(path)["duration"]
    if d <= 0:
        raise FFmpegError(f"could not read duration of {path}")
    return d


_VENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", str(FPS)]


def speech_window(path: str | Path, total: float) -> tuple[float, float]:
    """(start, end) of the audible speech: leading/trailing silence removed. Falls back to (0, total)."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                              "silencedetect=noise=-38dB:d=0.2", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=120).stderr
    except Exception:
        return 0.0, total
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", out)]
    start, end = 0.0, total
    if starts and starts[0] <= 0.05 and ends:        # silence at the very beginning
        start = ends[0]
    if starts and (len(starts) > len(ends) or (ends and ends[-1] >= total - 0.2)) and starts[-1] > start:
        end = starts[-1]                             # silence that runs to the end of the file
    if end - start < total * 0.5:                    # implausible: ignore
        return 0.0, total
    return max(0.0, start), min(total, end)


def scene_from_clip(src: str | Path, dst: str | Path, duration: float) -> None:
    """Stock clip -> exactly `duration` seconds of 1080x1920, cropped to fill, slightly graded and darkened."""
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},"
          "eq=brightness=-0.05:contrast=1.05:saturation=1.08,format=yuv420p")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1", "-i", str(src),
         "-t", f"{duration:.3f}", "-an", "-vf", vf, *_VENC, str(dst)], 300)


def scene_from_image(src: str | Path, dst: str | Path, duration: float, variant: int = 0) -> None:
    """Still picture -> `duration` seconds of 1080x1920 with a slow Ken Burns move (zoom in / out, alternating)."""
    frames = max(2, int(round(duration * FPS)))
    zmax = 0.16
    z = (f"1+{zmax}*on/{frames}" if variant % 2 == 0 else f"1+{zmax}-{zmax}*on/{frames}")
    # alternate the drift direction too, so consecutive scenes do not look identical
    x = ("iw/2-(iw/zoom/2)+(iw*0.03)*(on/%d-0.5)" % frames) if variant % 3 == 0 else "iw/2-(iw/zoom/2)"
    vf = (f"crop=iw:ih*0.95:0:0,scale=1620:2880:force_original_aspect_ratio=increase,crop=1620:2880,"
          f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
          "setsar=1,eq=brightness=-0.03:contrast=1.06:saturation=1.1,format=yuv420p")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf", vf,
         "-frames:v", str(frames), "-an", *_VENC, str(dst)], 300)


GENERATED_PALETTES = {
    "dark-blue": ("0x0b1f4d", "0x1d6fa5", "0x120a3a"),
    "warm": ("0x3b0d2e", "0xc2410c", "0x7c2d12"),
    "neon": ("0x0a0a23", "0x6d28d9", "0x0e7490"),
}


def scene_generated(dst: str | Path, duration: float, style: str, variant: int = 0) -> None:
    """Licence-free animated gradient background (fallback when no stock footage is available)."""
    c = GENERATED_PALETTES.get(style, GENERATED_PALETTES["dark-blue"])
    c0, c1 = (c[variant % 3], c[(variant + 1) % 3])
    src = f"gradients=s={W}x{H}:c0={c0}:c1={c1}:x0=0:y0=0:x1={W}:y1={H}:speed=0.04:duration={duration:.3f}:rate={FPS}"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", src,
         "-vf", "format=yuv420p", *_VENC, str(dst)], 300)


def concat_with_transitions(scenes: list[str | Path], durations: list[float], dst: str | Path,
                            fade: float = 0.35, seed: int = 0) -> None:
    """Join scene clips with xfade transitions. Scene i must be durations[i] (+ `fade` for all but the last)."""
    n = len(scenes)
    if n == 0:
        raise FFmpegError("no scenes to join")
    if n == 1:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(scenes[0]), "-c", "copy", str(dst)], 120)
        return
    inputs: list[str] = []
    for s in scenes:
        inputs += ["-i", str(s)]
    parts, last, cum = [], "[0:v]", 0.0
    for i in range(1, n):
        cum += durations[i - 1]
        out = f"[x{i}]" if i < n - 1 else "[v]"
        tr = TRANSITIONS[(seed + i) % len(TRANSITIONS)]
        parts.append(f"{last}[{i}:v]xfade=transition={tr}:duration={fade:.3f}:offset={cum:.3f}{out}")
        last = out
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", ";".join(parts),
         "-map", "[v]", "-an", *_VENC, str(dst)], 900)


def _ass_filter_path(p: str | Path) -> str:
    # filtergraph escaping for the ass filter
    s = str(p).replace("\\", "/")
    return re.sub(r"([:'\[\],;])", r"\\\1", s)


def final_mix(video: str | Path, audio: str | Path, ass: Optional[str | Path], dst: str | Path,
              total: float) -> None:
    """Burn captions, mix the voice-over (loudness-normalised), faststart MP4."""
    vf = ["format=yuv420p"]
    if ass:
        vf.insert(0, f"ass={_ass_filter_path(ass)}")
    af = "loudnorm=I=-16:TP=-1.5:LRA=11,apad"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-i", str(audio),
         "-vf", ",".join(vf), "-af", af, "-map", "0:v:0", "-map", "1:a:0", *_VENC,
         "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", "-t", f"{total:.3f}",
         "-movflags", "+faststart", str(dst)], 1200)


def extract_frame(video: str | Path, dst: str | Path, at: float = 1.0) -> None:
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(video),
         "-frames:v", "1", "-q:v", "2", str(dst)], 60)
