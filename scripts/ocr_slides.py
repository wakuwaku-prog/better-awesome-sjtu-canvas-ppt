#!/usr/bin/env python
"""OCR slide images extracted from a classroom recording.

Reads the manifest written by ``extract_ppt_frames.py``, runs EasyOCR
(Chinese simplified + English) on every slide image and writes:

* ``<label>_ocr.json``  — per-slide text lines, boxes and page-number candidates
* ``<label>_ocr.md``    — human-readable dump used to write the notes

The deck used in HLT3202G prints its page number in the bottom band of every
slide, right after the running footer. ``page_guess`` picks the last standalone
integer found in the bottom 12% of the image, which reproduces the confirmed
page numbers of the L01 deck (p1, p8, p10, p12, p28, p29).

Usage
-----
python ocr_slides.py <manifest.json> [--gpu] [--outdir DIR]
"""
import argparse
import json
import os
import re
import sys

import numpy as np

INT_RE = re.compile(r"^\s*(\d{1,3})\s*$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--lang", default="ch_sim,en")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    with open(args.manifest, encoding="utf-8") as f:
        man = json.load(f)
    base = os.path.dirname(os.path.abspath(args.manifest))
    outdir = args.outdir or base
    os.makedirs(outdir, exist_ok=True)

    import easyocr
    from PIL import Image

    reader = easyocr.Reader(args.lang.split(","), gpu=args.gpu, verbose=False)

    results = []
    for sp in man["slides"]:
        path = os.path.join(base, sp["file"])
        if not os.path.exists(path):
            continue
        with Image.open(path) as im:
            W, H = im.size
            # cv2.imread cannot open non-ASCII Windows paths, so hand EasyOCR
            # an already-decoded array instead of a filename.
            arr = np.asarray(im.convert("RGB"))
        raw = reader.readtext(arr, detail=1, paragraph=False)
        lines = []
        for box, text, conf in raw:
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            lines.append({
                "text": text.strip(),
                "conf": round(float(conf), 3),
                "top": round(min(ys) / H, 4),
                "left": round(min(xs) / W, 4),
            })
        lines.sort(key=lambda d: (round(d["top"], 2), d["left"]))

        page_guess = None
        bottom = [l for l in lines if l["top"] > 0.88]
        for l in reversed(bottom):
            m = INT_RE.match(l["text"])
            if m and l["conf"] > 0.30:
                page_guess = int(m.group(1))
                break
        if page_guess is None:
            for l in reversed(lines):
                m = INT_RE.match(l["text"])
                if m and l["top"] > 0.80 and l["conf"] > 0.30:
                    page_guess = int(m.group(1))
                    break

        results.append({
            "index": sp["index"], "file": sp["file"],
            "start": sp["start"], "end": sp.get("end"),
            "start_mmss": sp["start_mmss"],
            "duration": sp.get("duration",
                              round((sp["end"] - sp["start"]), 1)
                              if sp.get("end") is not None else None),
            "page_guess": page_guess,
            "text": " ".join(l["text"] for l in lines),
            "lines": lines,
        })
        print(f"  [{sp['index']:03d}] {sp['start_mmss']} page={page_guess} "
              f"lines={len(lines)}")

    payload = {"label": man["label"], "video": man.get("video"),
               "n_slides": len(results), "slides": results}
    json_path = os.path.join(outdir, f"{man['label']}_ocr.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    md_path = os.path.join(outdir, f"{man['label']}_ocr.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {man['label']} — 课件画面 OCR\n\n")
        f.write(f"- 视频 Video: `{man.get('video')}`\n")
        f.write(f"- 幻灯片数 Slides: {len(results)}\n")
        f.write("- OCR: EasyOCR (ch_sim + en)，机器识别，页码为页脚推定值\n\n")
        for r in results:
            f.write(f"## slide_{r['index']:03d} — {r['start_mmss']} "
                    f"(page≈{r['page_guess']})\n\n")
            for l in r["lines"]:
                f.write(f"- {l['text']}\n")
            f.write("\n")

    print(f"-> {json_path}")
    print(f"-> {md_path}")


if __name__ == "__main__":
    main()
