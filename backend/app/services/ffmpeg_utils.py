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


def scene_from_clip(src: str | Path, dst: str | Path, duration: float, size: tuple[int, int] = (W, H)) -> None:
    """Stock clip -> exactly `duration` seconds of 1080x1920 (or `size`), cropped to fill, slightly graded."""
    w, h = size
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={FPS},"
          "eq=brightness=-0.05:contrast=1.05:saturation=1.08,format=yuv420p")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1", "-i", str(src),
         "-t", f"{duration:.3f}", "-an", "-vf", vf, *_VENC, str(dst)], 300)


def scene_from_image(src: str | Path, dst: str | Path, duration: float, variant: int = 0,
                     size: tuple[int, int] = (W, H)) -> None:
    """Still picture -> `duration` seconds of 1080x1920 (or `size`) with a slow Ken Burns move (zoom in / out)."""
    w, h = size
    frames = max(2, int(round(duration * FPS)))
    zmax = 0.16
    z = (f"1+{zmax}*on/{frames}" if variant % 2 == 0 else f"1+{zmax}-{zmax}*on/{frames}")
    # alternate the drift direction too, so consecutive scenes do not look identical
    x = ("iw/2-(iw/zoom/2)+(iw*0.03)*(on/%d-0.5)" % frames) if variant % 3 == 0 else "iw/2-(iw/zoom/2)"
    big_w, big_h = int(w * 1.5), int(h * 1.5)
    vf = (f"crop=iw:ih*0.95:0:0,scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h},"
          f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={FPS},"
          "setsar=1,eq=brightness=-0.03:contrast=1.06:saturation=1.1,format=yuv420p")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf", vf,
         "-frames:v", str(frames), "-an", *_VENC, str(dst)], 300)


def scene_from_image_shots(src: str | Path, dst: str | Path, duration: float, variant: int = 0,
                           max_shot: float = 2.8, size: tuple[int, int] = (W, H), max_shots: int = 3) -> None:
    """Like scene_from_image, but long scenes are cut into several shots (different zoom/drift) for pace."""
    n = shot_count(duration, max_shot, max_shots)
    if n == 1:
        return scene_from_image(src, dst, duration, variant, size)
    d = Path(dst)
    parts, each = [], duration / n
    try:
        for k in range(n):
            p = d.with_name(f"{d.stem}_shot{k}.mp4")
            scene_from_image(src, p, each, variant + k, size)
            parts.append(p)
        lst = d.with_name(f"{d.stem}_shots.txt")
        lst.write_text("".join(f"file '{p.name}'\n" for p in parts), "utf-8")
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-c", "copy", str(dst)], 120)
        lst.unlink(missing_ok=True)
    finally:
        for p in parts:
            p.unlink(missing_ok=True)


GENERATED_PALETTES = {
    "dark-blue": ("0x0b1f4d", "0x1d6fa5", "0x120a3a"),
    "warm": ("0x3b0d2e", "0xc2410c", "0x7c2d12"),
    "neon": ("0x0a0a23", "0x6d28d9", "0x0e7490"),
}


def scene_generated(dst: str | Path, duration: float, style: str, variant: int = 0,
                    size: tuple[int, int] = (W, H)) -> None:
    """Licence-free animated gradient background (fallback when no stock footage is available)."""
    c = GENERATED_PALETTES.get(style, GENERATED_PALETTES["dark-blue"])
    c0, c1 = (c[variant % 3], c[(variant + 1) % 3])
    w, h = size
    src = f"gradients=s={w}x{h}:c0={c0}:c1={c1}:x0=0:y0=0:x1={w}:y1={h}:speed=0.04:duration={duration:.3f}:rate={FPS}"
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


def concat_hard(scenes: list[str | Path], dst: str | Path) -> None:
    """Join scene clips that were encoded with identical settings by plain concatenation (no re-encode).

    Used for long videos: dozens of xfade transitions would make the render far too slow."""
    if not scenes:
        raise FFmpegError("no scenes to join")
    d = Path(dst)
    lst = d.with_name(f"{d.stem}_concat.txt")
    lst.write_text("".join("file '" + str(Path(p).resolve()).replace("'", "'\\''") + "'\n" for p in scenes), "utf-8")
    try:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-c", "copy", "-an", str(dst)], 600)
    finally:
        lst.unlink(missing_ok=True)


def _ass_filter_path(p: str | Path) -> str:
    # filtergraph escaping for the ass filter
    s = str(p).replace("\\", "/")
    return re.sub(r"([:'\[\],;])", r"\\\1", s)


_CHORDS = [(110.0, 164.81, 220.0, 277.18), (87.31, 130.81, 174.61, 220.0),
           (130.81, 196.0, 261.63, 329.63), (98.0, 146.83, 196.0, 246.94)]


def make_music(dst: str | Path, duration: float, variant: int = 0) -> None:
    """Soft synthetic ambient pad (original, royalty-free by construction)."""
    freqs = _CHORDS[variant % len(_CHORDS)]
    d = f"{duration + 1:.2f}"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for f in freqs:
        cmd += ["-f", "lavfi", "-i", f"sine=f={f}:d={d}:sample_rate=44100"]
    fo = max(0.0, duration - 2.0)
    fc = ("[0][1][2][3]amix=inputs=4:normalize=0,tremolo=f=0.25:d=0.35,lowpass=f=1400,"
          f"afade=t=in:d=1.5,afade=t=out:st={fo:.2f}:d=2,aformat=channel_layouts=stereo[m]")
    run([*cmd, "-filter_complex", fc, "-map", "[m]", "-t", d, str(dst)], 120)


WHOOSH_SECONDS = 0.5
MAX_SFX_HITS = 24


def make_whoosh(dst: str | Path) -> None:
    """Original "вжух" swoosh: a band-passed noise swell plus a rising chirp (synthetic, royalty-free)."""
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"anoisesrc=d={WHOOSH_SECONDS}:c=pink:r=44100:a=0.8",
         "-f", "lavfi", "-i", f"aevalsrc=exprs='0.35*sin(2*PI*(260*t+3200*t*t))':d={WHOOSH_SECONDS}:s=44100",
         "-filter_complex",
         "[0]highpass=f=400,lowpass=f=6000,afade=t=in:d=0.2,afade=t=out:st=0.2:d=0.3[n];"
         "[1]afade=t=in:d=0.12,afade=t=out:st=0.18:d=0.32,lowpass=f=2500[c];"
         "[n][c]amix=inputs=2:normalize=0,volume=1.2,alimiter=limit=0.9,"
         "aformat=sample_rates=44100:channel_layouts=stereo[w]",
         "-map", "[w]", "-t", f"{WHOOSH_SECONDS}", str(dst)], 60)


def make_sfx_track(dst: str | Path, whoosh: str | Path, times: list[float], total: float) -> int:
    """One stereo track of `total` seconds with a whoosh starting at each time (seconds). Returns the hit count."""
    hits = sorted({round(max(0.0, t), 2) for t in times if 0.0 <= t < total - 0.15})[:MAX_SFX_HITS]
    if not hits:
        return 0
    n = len(hits)
    split = "".join(f"[w{i}]" for i in range(n))
    parts = [f"[0:a]asplit={n}{split}" if n > 1 else "[0:a]anull[w0]"]
    for i, t in enumerate(hits):
        ms = int(t * 1000)
        parts.append(f"[w{i}]adelay={ms}|{ms}[d{i}]")
    mix_in = "".join(f"[d{i}]" for i in range(n))
    parts.append(f"{mix_in}amix=inputs={n}:normalize=0,apad[sfx]")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(whoosh), "-filter_complex", ";".join(parts),
         "-map", "[sfx]", "-t", f"{total:.3f}", "-ar", "44100", "-ac", "2", str(dst)], 120)
    return n


def shot_count(duration: float, max_shot: float = 2.8, max_shots: int = 3) -> int:
    """How many shots scene_from_image_shots cuts a scene of this length into (kept in one place)."""
    n = int(duration // max_shot) if duration > max_shot * 1.25 else 1
    return min(max(n, 1), max_shots)


def final_mix(video: str | Path, audio: str | Path, ass: Optional[str | Path], dst: str | Path,
              total: float, music: Optional[str | Path] = None, music_volume: float = 0.12,
              sfx: Optional[str | Path] = None, sfx_volume: float = 0.5) -> None:
    """Burn captions, mix the voice-over (loudness-normalised), optional ducked music and cut sound effects."""
    vf = ["format=yuv420p"]
    if ass:
        vf.insert(0, f"ass={_ass_filter_path(ass)}")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-i", str(audio)]
    idx = 2
    music_idx = sfx_idx = None
    if music:
        cmd += ["-i", str(music)]
        music_idx, idx = idx, idx + 1
    if sfx:
        cmd += ["-i", str(sfx)]
        sfx_idx, idx = idx, idx + 1
    if not music and not sfx:
        cmd += ["-vf", ",".join(vf), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,apad",
                "-map", "0:v:0", "-map", "1:a:0"]
    else:
        fc = [f"[0:v]{','.join(vf)}[v]"]
        if music:
            fc.append("[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,apad,asplit=2[vo][sc]")
            fc.append(f"[{music_idx}:a]volume={music_volume:.3f}[bg]")
            fc.append("[bg][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]")
            layers = ["[vo]", "[duck]"]
        else:
            fc.append("[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,apad[vo]")
            layers = ["[vo]"]
        if sfx:
            fc.append(f"[{sfx_idx}:a]volume={sfx_volume:.3f}[fx]")
            layers.append("[fx]")
        fc.append(f"{''.join(layers)}amix=inputs={len(layers)}:normalize=0:duration=first,"
                  "alimiter=limit=0.95:level=0[a]")
        cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]"]
    cmd += [*_VENC, "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", "-t", f"{total:.3f}",
            "-movflags", "+faststart", str(dst)]
    run(cmd, 1200)


def extract_frame(video: str | Path, dst: str | Path, at: float = 1.0) -> None:
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(video),
         "-frames:v", "1", "-q:v", "2", str(dst)], 60)
