"""Collapse consecutive near-duplicate slides from a scene-OCR run.

Scene detection fires on PowerPoint animation builds as well as on real slide
changes, so a scene-OCR run contains runs of frames showing the same slide.
This script keeps the LAST frame of every such run (the most complete build) and
reports the text-similarity groups so the surviving frames can be named and
archived.

Usage: python dedupe_slides.py <label>_ocr.json [--thresh 0.80] [--out kept.json]
"""
import argparse
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

TOKEN = re.compile(r"[A-Za-z\u4e00-\u9fff]{2,}")


def tokens(text):
    return set(t.lower() for t in TOKEN.findall(text or ""))


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


ap = argparse.ArgumentParser()
ap.add_argument("ocr_json")
ap.add_argument("--thresh", type=float, default=0.80)
ap.add_argument("--out", default=None)
args = ap.parse_args()

data = json.load(open(args.ocr_json, encoding="utf-8"))
slides = data["slides"]

groups = []
for s in slides:
    s["_tok"] = tokens(s["text"])
    if groups and jaccard(groups[-1][-1]["_tok"], s["_tok"]) >= args.thresh:
        groups[-1].append(s)
    else:
        groups.append([s])

kept = []
for g in groups:
    best = max(g, key=lambda s: len(s["lines"]))
    kept.append({"index": best["index"], "file": best["file"],
                 "start": best["start"], "start_mmss": best["start_mmss"],
                 "page_guess": best.get("page_guess"),
                 "n_frames_in_group": len(g),
                 "group_files": [x["file"] for x in g],
                 "title": (best["lines"][1]["text"] if len(best["lines"]) > 1
                           else (best["lines"][0]["text"] if best["lines"] else "")),
                 "text": best["text"]})

print("%d scene frames -> %d distinct slides" % (len(slides), len(kept)))
for k in kept:
    print("  keep %-14s %s  x%-3d page=%-4s %s"
          % (k["file"], k["start_mmss"], k["n_frames_in_group"],
             k["page_guess"], k["title"][:60]))

target = args.out or (args.ocr_json.replace("_ocr.json", "_kept.json"))
with open(target, "w", encoding="utf-8") as handle:
    json.dump({"label": data["label"], "n_kept": len(kept), "kept": kept},
              handle, ensure_ascii=False, indent=1)
print("-> " + target)
