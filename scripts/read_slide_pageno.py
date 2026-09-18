#!/usr/bin/env python
"""Read the printed page number from the footer band of slide images.

The HLT3202G deck prints ``PSYCHOLOGY AND ITS HISTORY 心理学与心理学史 <n>``
in a footer band at the bottom of every slide, with ``<n>`` at the far right.
The number is small, so it is cropped, upscaled and re-OCR'd with a digit-only
whitelist; this is far more reliable than reading it from a full-frame pass.

Usage
-----
python read_slide_pageno.py <manifest.json> [--gpu] [--out FILE]
"""
import argparse
import json
import os
import re
import sys

import numpy as np
from PIL import Image

DIGITS = re.compile(r"(\d{1,3})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--bottom", type=float, default=0.10, help="footer band height fraction")
    ap.add_argument("--right", type=float, default=0.30, help="footer band width fraction")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    with open(args.manifest, encoding="utf-8") as f:
        man = json.load(f)
    base = os.path.dirname(os.path.abspath(args.manifest))

    import easyocr
    reader = easyocr.Reader(["en"], gpu=args.gpu, verbose=False)

    out = []
    for sp in man["slides"]:
        path = os.path.join(base, sp["file"])
        if not os.path.exists(path):
            continue
        with Image.open(path) as im:
            W, H = im.size
            box = (int(W * (1 - args.right)), int(H * (1 - args.bottom)), W, H)
            crop = im.crop(box).convert("L")
            crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
            arr = np.asarray(crop.convert("RGB"))
        res = reader.readtext(arr, detail=1, allowlist="0123456789",
                              text_threshold=0.5, low_text=0.3)
        cands = []
        for b, t, c in res:
            m = DIGITS.search(t)
            if m:
                right_edge = max(p[0] for p in b) / arr.shape[1]
                cands.append({"n": int(m.group(1)), "conf": round(float(c), 3),
                              "right": round(right_edge, 3)})
        cands.sort(key=lambda d: -d["right"])
        best = cands[0]["n"] if cands else None
        out.append({"index": sp["index"], "file": sp["file"],
                    "start_mmss": sp["start_mmss"], "page": best, "cands": cands})
        print(f"  [{sp['index']:03d}] {sp['start_mmss']} page={best} "
              f"cands={[(c['n'], c['conf']) for c in cands[:3]]}")

    target = args.out or os.path.join(base, f"{man['label']}_pageno.json")
    with open(target, "w", encoding="utf-8") as f:
        json.dump({"label": man["label"], "slides": out}, f, ensure_ascii=False, indent=1)
    print(f"-> {target}")


if __name__ == "__main__":
    main()
