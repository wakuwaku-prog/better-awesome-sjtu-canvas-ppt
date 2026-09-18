"""List scene-change timestamps and scores for a video.

Uses ffmpeg's scene detector, which decodes the file in order and therefore
stays accurate even when the container timestamps are unreliable (some lecture
screen recordings have duplicate/gappy timestamps, which makes ``-ss`` seeking
land on the wrong frame).

Usage: python scene_times.py <video> [--thresh 0.02] [--out file]
"""
import argparse
import re
import subprocess
import sys

PAT = re.compile(r"pts_time:([0-9.]+)")

ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("--thresh", type=float, default=0.02)
ap.add_argument("--out", default=None)
args = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")

cmd = ["ffmpeg", "-v", "info", "-i", args.video,
       "-vf", "select='gt(scene," + str(args.thresh) + ")',showinfo",
       "-an", "-f", "null", "-"]
proc = subprocess.run(cmd, stderr=subprocess.PIPE)
text = proc.stderr.decode("utf-8", "replace")

rows = []
for line in text.splitlines():
    found = PAT.search(line)
    if found is None:
        continue
    mean = re.search(r"mean:\[([0-9.]+)", line)
    rows.append((float(found.group(1)), float(mean.group(1)) if mean else 0.0))

rows.sort()
target = args.out if args.out else (args.video + ".scenes.txt")
with open(target, "w", encoding="utf-8") as handle:
    for t, sc in rows:
        handle.write("%.3f\t%.3f\n" % (t, sc))
print("%d scene changes -> %s" % (len(rows), target))
