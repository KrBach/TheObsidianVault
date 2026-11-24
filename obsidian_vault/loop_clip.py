"""Make a portrait clip loop without a visible jump, and shrink it for the game.

Run: python3 -m obsidian_vault.loop_clip in.mp4 web/portraits/tally-think.mp4 [--fade 0.5] [--width 480]

Needs ffmpeg. It measures how different the first and last frames are (the "seam"),
then cross-fades the end of the clip into its start, so the last frame leads straight
back into the first. The result has no audio, is scaled to --width, and starts fast
in the browser (faststart).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

SIZE = 32  # seam check compares tiny greyscale frames


def duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def frame(path: str, at: float) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(at, 0):.3f}", "-i", path, "-frames:v", "1",
         "-vf", f"scale={SIZE}:{SIZE},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout


def seam(path: str) -> float:
    """Mean difference (0-100 %) between the first and the last frame."""
    a, b = frame(path, 0), frame(path, duration(path) - 0.05)
    if not a or not b:
        return 100.0
    return 100 * sum(abs(x - y) for x, y in zip(a, b)) / (255 * len(a))


def make_loop(src: str, dst: str, fade: float, width: int) -> None:
    length = duration(src)
    fade = min(fade, length / 3)
    graph = (
        f"[0:v]trim=start={fade},setpts=PTS-STARTPTS[body];"
        f"[0:v]trim=end={fade},setpts=PTS-STARTPTS[head];"
        f"[body][head]xfade=transition=fade:duration={fade}:offset={length - 2 * fade},"
        f"scale={width}:-2,format=yuv420p[v]"
    )
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-filter_complex", graph, "-map", "[v]", "-an",
                    "-c:v", "libx264", "-crf", "24", "-preset", "slow", "-movflags", "+faststart", dst], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--fade", type=float, default=0.5, help="seconds of cross-fade at the seam (default 0.5)")
    ap.add_argument("--width", type=int, default=480, help="output width in pixels (default 480)")
    args = ap.parse_args()
    if not shutil.which("ffmpeg"):
        print("ffmpeg is needed: brew install ffmpeg")
        return 1
    before = seam(args.src)
    make_loop(args.src, args.dst, args.fade, args.width)
    after = seam(args.dst)
    print(f"seam before: {before:.1f}% different, after: {after:.1f}% ({args.dst})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
