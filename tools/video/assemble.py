"""Transcode the raw Playwright .webm into docs/video/home-apm-demo.mp4 (H.264, 1080p).

Playwright records one continuous VP8 .webm; this is a single re-encode to a
broadly-compatible H.264/yuv420p MP4 at a fixed 30fps and 1920x1080.

    python tools/video/assemble.py [path-to-raw.webm]

If no path is given, reads tools/video/_work/last_raw.txt (written by record_demo.py).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(r"C:\Users\abhis\Desktop\OSS\Signoz\Track3\home-apm")
WORK = REPO / "tools" / "video" / "_work"
OUT = REPO / "docs" / "video" / "home-apm-demo.mp4"


def ffmpeg() -> str:
    from shutil import which

    exe = which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


def main() -> int:
    if len(sys.argv) > 1:
        raw = Path(sys.argv[1])
    else:
        raw = Path((WORK / "last_raw.txt").read_text(encoding="utf-8").strip())
    if not raw.exists():
        print(f"ERROR: raw video not found: {raw}")
        return 2

    OUT.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg(), "-y",
        "-i", str(raw),
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
               "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-movflags", "+faststart",
        "-an",
        str(OUT),
    ]
    print("running:", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-2000:])
        return proc.returncode
    print(f"DONE -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
