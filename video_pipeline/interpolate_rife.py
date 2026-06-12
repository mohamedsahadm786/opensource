"""
video_pipeline/interpolate_rife.py — RIFE frame interpolation (16 -> 32 fps).

Doubles (or quadruples) a clip's frame rate by drawing the in-between frames with
RIFE (Practical-RIFE). Smoothness only — it adds no detail; it removes the 16 fps
stop-motion "AI look" at a tiny fraction of diffusion cost.

MUST be applied PER CLIP (before any concat across shots): interpolating across a
hard cut would blend two unrelated frames into a ghost. Internal frame_join seams
are acceptable (framematch seams blend near-identical frames; a punchin_fallback
seam yields one 1/32 s blended frame).

Backend: the Practical-RIFE repo (https://github.com/hzwer/Practical-RIFE) run as
a subprocess. Pod setup (claudeAI.md TASK 3) clones it + downloads the model.
  RIFE_DIR    env — Practical-RIFE checkout (default /workspace/Practical-RIFE)
  RIFE_PYTHON env — python to run it with   (default: this process's interpreter)

Public surface:
  interpolate(in_path, out_path, *, target_fps=32, scene_id="?") -> dict
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RIFE_DIR = Path(os.environ.get("RIFE_DIR", "/workspace/Practical-RIFE"))
RIFE_PYTHON = os.environ.get("RIFE_PYTHON", sys.executable)

_NULL = subprocess.DEVNULL


def _probe(path: Path, entries: str, stream: str | None = None) -> str:
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    return subprocess.check_output(cmd).decode().strip()


def _src_fps(path: Path) -> float:
    raw = _probe(path, "stream=avg_frame_rate", stream="v:0").splitlines()[0]
    num, _, den = raw.partition("/")
    return float(num) / float(den or 1)


def _has_audio(path: Path) -> bool:
    try:
        return bool(_probe(path, "stream=codec_type", stream="a"))
    except subprocess.CalledProcessError:
        return False


def available() -> bool:
    return (RIFE_DIR / "inference_video.py").exists()


def interpolate(in_path, out_path, *, target_fps: int = 32, scene_id: str = "?") -> dict:
    """Interpolate ONE clip to ~target_fps. Audio (if any) is preserved.
    Raises if the RIFE checkout is missing — callers gate on the tenant knob,
    so a hard error beats silently shipping 16 fps when 32 was requested."""
    in_path, out_path = Path(in_path), Path(out_path)
    if not available():
        raise RuntimeError(
            f"Practical-RIFE not found at {RIFE_DIR} (set RIFE_DIR / see claudeAI.md TASK 3)")

    src_fps = _src_fps(in_path)
    factor = max(2, min(4, round(target_fps / max(1.0, src_fps))))

    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="rife_") as td:
        work = Path(td)
        tmp_in = work / f"in{in_path.suffix}"
        shutil.copy(in_path, tmp_in)
        # inference_video.py writes its result NEXT TO the input (name varies by
        # version), so run it against a private temp copy and glob for the output.
        subprocess.run(
            [RIFE_PYTHON, str(RIFE_DIR / "inference_video.py"),
             f"--multi={factor}", f"--video={tmp_in}"],
            cwd=str(RIFE_DIR), check=True, stdout=_NULL, stderr=subprocess.STDOUT)
        produced = [p for p in work.glob("*") if p != tmp_in and p.suffix.lower() == ".mp4"]
        if not produced:
            raise RuntimeError(f"RIFE produced no output for {in_path.name}")
        result = max(produced, key=lambda p: p.stat().st_mtime)

        # Practical-RIFE transfers audio itself in most versions; mux as insurance.
        if _has_audio(in_path) and not _has_audio(result):
            muxed = work / "muxed.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(result), "-i", str(in_path),
                 "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                 "-b:a", "160k", "-shortest", str(muxed)],
                check=True, stdout=_NULL, stderr=_NULL)
            result = muxed

        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(result, out_path)

    out_fps = _src_fps(out_path)
    elapsed = time.time() - t0
    print(f"[rife] [{scene_id}] {src_fps:.0f} -> {out_fps:.0f} fps (x{factor}) in {elapsed:.1f}s")
    return {"local_path": str(out_path), "source_fps": src_fps, "fps": out_fps,
            "factor": factor, "elapsed_seconds": elapsed}
