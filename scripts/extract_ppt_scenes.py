#!/usr/bin/env python
"""Extract one image per slide from a classroom screen recording (decode-accurate).

Some SJTU lecture screen recordings carry unreliable container timestamps: an
``ffmpeg -ss T`` seek can land on a frame that belongs to a different moment, so
frames grabbed by seek may not match their nominal timestamp. This script avoids
seeking entirely:

1. one decode pass with ``select='gt(scene,TH)'`` + ``showinfo`` records the
   timestamp of every detected scene change;
2. a second pass with the same filter writes the first frame of every scene as
   ``slide_###.jpg``.

Because both passes use the same filter graph over the same decoded frames, the
n-th written image always corresponds to the n-th recorded timestamp.

Usage
-----
python extract_ppt_scenes.py <video.mp4> <outdir> <label> [--thresh 0.02]
"""
import argparse
import json
import os
import re
import subprocess
import sys

PAT = re.compile(r"pts_time:([0-9.]+)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("outdir")
    ap.add_argument("label")
    ap.add_argument("--thresh", type=float, default=0.02)
    ap.add_argument("--quality", type=int, default=2)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    os.makedirs(args.outdir, exist_ok=True)
    select = "select='gt(scene," + str(args.thresh) + ")'"

    print("[1/3] pass 1: locating scene changes ...")
    proc = subprocess.run(["ffmpeg", "-v", "info", "-i", args.video,
                           "-vf", select + ",showinfo", "-an", "-f", "null", "-"],
                          stderr=subprocess.PIPE)
    times = []
    for line in proc.stderr.decode("utf-8", "replace").splitlines():
        found = PAT.search(line)
        if found:
            times.append(round(float(found.group(1)), 3))
    times.sort()
    print("      %d scene changes" % len(times))

    print("[2/3] pass 2: writing one frame per scene ...")
    pattern = os.path.join(args.outdir, "slide_%03d.jpg")
    subprocess.run(["ffmpeg", "-v", "error", "-i", args.video, "-vf", select,
                    "-vsync", "0", "-q:v", str(args.quality), "-y", pattern],
                   check=True)
    written = sorted(f for f in os.listdir(args.outdir)
                     if f.startswith("slide_") and f.endswith(".jpg"))
    print("      %d images written" % len(written))

    n = min(len(times), len(written))
    if len(times) != len(written):
        print("      WARNING: %d timestamps vs %d images; pairing the first %d"
              % (len(times), len(written), n))

    print("[3/3] writing manifest ...")
    slides = []
    for i in range(n):
        t = times[i]
        mm, ss = int(t // 60), int(t % 60)
        slides.append({"index": i + 1, "file": written[i], "start": t,
                       "start_mmss": "%02d%02d" % (mm, ss),
                       "end": times[i + 1] if i + 1 < len(times) else None})
    payload = {"label": args.label, "video": os.path.basename(args.video),
               "thresh": args.thresh, "n_slides": len(slides), "slides": slides}
    target = os.path.join(args.outdir, args.label + ".json")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    print("      -> " + target)


if __name__ == "__main__":
    main()
