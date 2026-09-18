#!/usr/bin/env python
"""Extract one image per slide from a classroom screen-recording video.

When the SJTU replay platform has not generated the PPT track for a session
(``/v1/course/ai/ppt`` returns ``data: null`` and ``pptShow = 0``), the slides can
still be recovered from the *screen* camera of the recording: the smaller of the
two per-session mp4 files is a full-screen capture of the slide show.

Pipeline
--------
1. Sample one downscaled grayscale frame every ``--interval`` seconds with ffmpeg.
2. Group consecutive frames whose mean absolute difference is below ``--thresh``
   into a single "slide" span.
3. Drop spans shorter than ``--min-dur`` seconds (mouse motion, transitions).
4. Re-extract the first frame of every span at full resolution.

Outputs ``<outdir>/<label>.json`` (manifest) and ``slide_###.jpg`` images.

Usage
-----
python extract_ppt_frames.py <video.mp4> <outdir> <label> [--interval 2] [--thresh 0.02]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "replace")[-2000:])
    return p.stdout


def probe_duration(video):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "csv=p=0", video]).decode().strip()
    return float(out)


def sample_frames(video, tmpdir, interval, width):
    pattern = os.path.join(tmpdir, "s_%06d.png")
    run(["ffmpeg", "-v", "error", "-i", video, "-vf",
         f"fps=1/{interval},scale={width}:-2", "-y", pattern])
    files = sorted(f for f in os.listdir(tmpdir) if f.startswith("s_"))
    return [os.path.join(tmpdir, f) for f in files]


def frame_signature(path):
    from PIL import Image
    with Image.open(path) as im:
        g = im.convert("L").resize((160, 90))
        a = np.asarray(g, dtype=np.float32)
    return a


def group_slides(sigs, interval, thresh, min_dur, total_dur):
    spans = []
    if not sigs:
        return spans
    start_idx = 0
    for i in range(1, len(sigs)):
        diff = float(np.mean(np.abs(sigs[i] - sigs[i - 1]))) / 255.0
        if diff > thresh:
            spans.append((start_idx, i - 1, diff))
            start_idx = i
    spans.append((start_idx, len(sigs) - 1, 0.0))

    kept = []
    for a, b, diff in spans:
        t0, t1 = a * interval, (b + 1) * interval
        dur = t1 - t0
        if dur < min_dur and kept:
            # too short to be a slide of its own: extend the previous span
            kept[-1]["end"] = t1
            kept[-1]["n_frames"] += (b - a + 1)
            continue
        kept.append({"start": t0, "end": t1, "n_frames": b - a + 1,
                     "change": round(diff, 4)})
    if kept:
        kept[-1]["end"] = max(kept[-1]["end"], total_dur)
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("outdir")
    ap.add_argument("label")
    ap.add_argument("--interval", type=float, default=2.0, help="sampling step in seconds")
    ap.add_argument("--thresh", type=float, default=0.02, help="mean-abs-diff threshold (0-1)")
    ap.add_argument("--min-dur", type=float, default=4.0, help="shortest accepted slide span")
    ap.add_argument("--width", type=int, default=480, help="sampling width for the diff pass")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    os.makedirs(args.outdir, exist_ok=True)
    total = probe_duration(args.video)
    print(f"[1/4] {os.path.basename(args.video)} duration={total:.1f}s ({total/60:.1f} min)")

    tmpdir = tempfile.mkdtemp(prefix="pptframes_")
    try:
        print(f"[2/4] sampling every {args.interval:g}s at {args.width}px ...")
        files = sample_frames(args.video, tmpdir, args.interval, args.width)
        print(f"      {len(files)} sampled frames")
        sigs = [frame_signature(f) for f in files]

        print(f"[3/4] grouping (thresh={args.thresh}, min-dur={args.min_dur:g}s) ...")
        spans = group_slides(sigs, args.interval, args.thresh, args.min_dur, total)
        print(f"      {len(spans)} slide spans")

        print("[4/4] extracting full-resolution frames ...")
        manifest = []
        for n, sp in enumerate(spans, 1):
            name = f"slide_{n:03d}.jpg"
            path = os.path.join(args.outdir, name)
            run(["ffmpeg", "-v", "error", "-ss", f"{sp['start']:.2f}", "-i", args.video,
                 "-frames:v", "1", "-q:v", "2", "-y", path])
            mm, ss = int(sp["start"] // 60), int(sp["start"] % 60)
            manifest.append({
                "index": n, "file": name,
                "start": round(sp["start"], 2), "end": round(sp["end"], 2),
                "start_mmss": f"{mm:02d}{ss:02d}",
                "duration": round(sp["end"] - sp["start"], 1),
                "n_frames": sp["n_frames"], "change": sp["change"],
            })
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    payload = {"label": args.label, "video": os.path.basename(args.video),
               "duration": total, "interval": args.interval, "thresh": args.thresh,
               "n_slides": len(manifest), "slides": manifest}
    with open(os.path.join(args.outdir, f"{args.label}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"      -> {os.path.join(args.outdir, args.label)}.json  slides={len(manifest)}")


if __name__ == "__main__":
    main()
