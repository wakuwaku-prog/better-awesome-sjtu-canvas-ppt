#!/usr/bin/env python3
"""map_frames_to_pdf.py — 把回放 PPT 轨翻页帧映射到教师课件 PDF 的页码。

2026-10-31 在 HLT3206G L07（S56/AI19–AI20）全流程验证：203/203 帧通过 JPEG 签名
校验，感知哈希把帧与 PDF 页一一对应；未匹配帧目视核查后确认了 5 页课件外内容
（Nichols 1997、Kiss-shrink-run 论文页等）。

输入：
  --frames <dir>    PPT 轨帧目录（文件名建议 <session>_<idx>_t<秒>.jpg）
  --pdf <file>      教师课件 PDF（用于 pdftoppm 渲染基准页图）
  --out <json>      输出映射 JSON（默认 <frames>/../frame_map.json）

可选：
  --dpi 72          PDF 渲染 DPI（默认 72，与帧同数量级即可）
  --thresh 40       aHash 汉明距离阈值（0–256；≥阈值视为「无良好匹配」）
  --keep-render     保留临时渲染页图（默认删除）

流程：JPEG 签名校验 → pdftoppm 渲染 PDF → 每帧 16×16 aHash 对全页求最近页 →
按时间折叠连续同页段 → 打印每节折叠时间线与「无良好匹配」帧清单（这些帧需要
人工目视：课前签到桌面 / 动画播放 / 课件外内容都会落在这里）。

依赖：Pillow；pdftoppm（Poppler）在 PATH。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

from PIL import Image

NAME_RE = re.compile(r"^(?P<sid>.+)_(?P<idx>\d+)_t(?P<t>\d+)\.jpe?g$", re.IGNORECASE)


def ahash(im: Image.Image, size: int = 16) -> list[int]:
    g = im.convert("L").resize((size, size), Image.LANCZOS)
    px = list(g.get_flattened_data()) if hasattr(g, "get_flattened_data") else list(g.getdata())
    avg = sum(px) / len(px)
    return [1 if p > avg else 0 for p in px]


def hamming(a: list[int], b: list[int]) -> int:
    return sum(x != y for x, y in zip(a, b))


def main() -> int:
    ap = argparse.ArgumentParser(description="Frame→PDF page mapping via perceptual hash")
    ap.add_argument("--frames", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--dpi", type=int, default=72)
    ap.add_argument("--thresh", type=int, default=40)
    ap.add_argument("--keep-render", action="store_true")
    args = ap.parse_args()

    frames = sorted(
        f for f in glob.glob(os.path.join(args.frames, "*.jp*g")) if NAME_RE.match(os.path.basename(f))
    )
    if not frames:
        print("未找到命名如 <session>_<idx>_t<秒>.jpg 的帧文件", file=sys.stderr)
        return 2

    # 1) JPEG 签名校验
    bad = [f for f in frames if open(f, "rb").read(3) != b"\xff\xd8\xff"]
    print(f"frames: {len(frames)}  bad JPEG signature: {len(bad)}")
    for f in bad:
        print("  BAD", f)
    frames = [f for f in frames if f not in set(bad)]

    # 2) 渲染 PDF 基准页
    tmp = tempfile.mkdtemp(prefix="mfp_pdf_")
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(args.dpi), args.pdf, os.path.join(tmp, "pg")],
        check=True, capture_output=True,
    )
    pages = {}
    for p in glob.glob(os.path.join(tmp, "pg-*.png")):
        n = int(p.rsplit("-", 1)[1].split(".")[0])
        pages[n] = ahash(Image.open(p))
    if not pages:
        print("PDF 渲染失败（pdftoppm）", file=sys.stderr)
        return 2
    print(f"pdf pages rendered: {len(pages)}")

    # 3) 每帧求最近页
    mapping, unmatched, sessions = {}, [], {}
    for f in frames:
        name = os.path.basename(f)
        m = NAME_RE.match(name)
        sid, t = m.group("sid"), int(m.group("t"))
        h = ahash(Image.open(f))
        page, _best_hash = min(pages.items(), key=lambda kv: hamming(h, kv[1]))
        dist = hamming(h, pages[page])
        mapping[name] = {"session": sid, "t": t, "page": page, "dist": dist}
        sessions.setdefault(sid, []).append((t, page, dist))
        if dist >= args.thresh:
            unmatched.append((name, sid, t, page, dist))

    # 4) 折叠连续同页段并打印时间线
    mmss = lambda s: f"{s // 60:02d}:{s % 60:02d}"
    for sid in sorted(sessions):
        rows = sorted(sessions[sid])
        collapsed = []
        for t, page, dist in rows:
            if collapsed and collapsed[-1][0] == page and dist < args.thresh:
                continue
            collapsed.append((page, t, dist))
        print(f"--- {sid}: {len(rows)} frames, segments {len(collapsed)}")
        print("; ".join(f"{mmss(t)} p{page}" for page, t, _ in collapsed))

    print()
    print(f"frames without good match (dist>={args.thresh}): {len(unmatched)}  ← 需人工目视（课前桌面/动画/课件外内容）")
    for name, sid, t, page, dist in unmatched:
        print(f"  {name}  closest p{page} dist {dist}")

    out = args.out or os.path.join(os.path.dirname(args.frames.rstrip("/\\")), "frame_map.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, ensure_ascii=False, indent=0)
    print("mapping written to", out)

    if not args.keep_render:
        for p in glob.glob(os.path.join(tmp, "*")):
            os.remove(p)
        os.rmdir(tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
