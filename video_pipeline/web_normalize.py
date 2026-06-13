"""
video_pipeline/web_normalize.py — guarantee a browser-playable final video.

Browsers decode only a narrow set of codecs in a <video> tag: H.264 (avc1) video
with yuv420p pixels, and AAC/MP3/Opus audio. Anything else — notably the mp4v
(MPEG-4 Part 2) that OpenCV/Practical-RIFE writes, or an exotic ComfyUI SaveVideo
codec — renders as a BLACK frame with audio-only in the web console and the
Supabase storage preview, while still playing fine in desktop players (VLC). That
mismatch is exactly the "plays after download, black in the browser" bug.

This module is the LAST step before a final video is uploaded. It inspects the
real streams and:
  - if already web-safe, does a near-instant stream-copy remux that only moves the
    moov atom to the front (+faststart) for progressive playback — no quality loss;
  - otherwise re-encodes the video to H.264/yuv420p (and audio to AAC).

It is idempotent and cheap on already-conformant files, so it is safe to call on
EVERY final video in EVERY mode (multishot, silentfirst, with or without RIFE).

Public surface:
  ensure_web_playable(path, out_path=None, *, scene_id="?") -> str
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_NULL = subprocess.DEVNULL

# Audio codecs a browser <video> tag can decode (video is always normalized to h264).
_WEB_AUDIO = {"aac", "mp3", "opus"}


def _probe(path: Path, entries: str, stream: str) -> str:
    try:
        return subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", stream,
             "-show_entries", entries, "-of",
             "default=noprint_wrappers=1:nokey=1", str(path)]).decode().strip()
    except subprocess.CalledProcessError:
        return ""


def ensure_web_playable(path, out_path=None, *, scene_id: str = "?") -> str:
    """Return a path whose mp4 is guaranteed to play in a browser <video> tag.

    Overwrites in place when out_path is None (via a temp file). The common case —
    a clip that is already H.264/yuv420p — costs one fast stream copy, so this is
    safe to call unconditionally as the final step of any video build."""
    path = Path(path)
    vcodec = _probe(path, "stream=codec_name", "v:0")
    pix_fmt = _probe(path, "stream=pix_fmt", "v:0")
    acodec = _probe(path, "stream=codec_name", "a:0")
    has_audio = bool(acodec)

    web_safe = (vcodec == "h264"
                and pix_fmt in ("yuv420p", "yuvj420p")
                and (not has_audio or acodec in _WEB_AUDIO))

    dest = Path(out_path) if out_path else path
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".webnorm_tmp.mp4")

    if web_safe:
        # Container-only fix: copy streams, relocate the moov atom to the front.
        cmd = ["ffmpeg", "-y", "-i", str(path), "-c", "copy",
               "-movflags", "+faststart", str(tmp)]
        action = "remux (+faststart)"
    else:
        cmd = ["ffmpeg", "-y", "-i", str(path), "-c:v", "libx264", "-crf", "18",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        cmd += ["-c:a", "aac", "-b:a", "160k"] if has_audio else ["-an"]
        cmd += [str(tmp)]
        action = f"re-encode ({vcodec or 'no-video'}/{pix_fmt or '?'} -> h264/yuv420p)"

    subprocess.run(cmd, check=True, stdout=_NULL, stderr=_NULL)
    os.replace(tmp, dest)
    print(f"[web_normalize] [{scene_id}] {action} -> {dest.name}")
    return str(dest)


if __name__ == "__main__":
    import sys
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else None
    print(ensure_web_playable(src, dst, scene_id="cli"))
