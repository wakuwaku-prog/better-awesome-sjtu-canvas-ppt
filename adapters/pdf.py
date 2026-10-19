#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""教师 PDF -> 渲染页图采集适配器（L0 采集层，``source: "pdf-render"``）。

适用：只有教师 PDF、没有回放（或回放未覆盖）时的第四条素材路线。
产出：``raw/pdf-pages/PDF pNN.jpg`` + ``raw/pdf.json`` + ``raw/pdf/slides.json`` + ``raw/source.json``。

用法
----
::

    python pdf.py --pdf "<课件.pdf>" --out <work>
        [--is-selection] [--dpi 150] [--no-render]
        [--mark-content-type 13=video] [--flag-page 14="文本顺序错乱：两方框标题与正文交错"]
        [--footer-unit "KNOWLEDGE REPRESENTATION"] [--page-no-scope section]
        [--page-no-map <page_no.json>] [--title-map <titles.json>]

PDF 阅读页 vs 幻灯片印刷页码
----------------------------
``pdfPage`` **一律是 PDF 阅读器页序**（1-based），**不是幻灯片印刷的页脚数字**。
两者不同是常态：L08 的 p24 页脚印 42、p10 页脚印「实验心理学 11」，
而阅读页分别是 24 与 10。页脚读数请走 ``--page-no-map`` 人工登记，
**本脚本不猜**——页脚不是页码，是**小节 / 版式编号**（L08 实测 p26–p31 与 p33 页脚都是 10）。

``isSelection``
---------------
``--is-selection`` 表示**这份 PDF 是选页而非课件全部**。L08 的 S84 就是选页：
p35 之后课堂还放了两页（第16节 46:41 / 48:28），不在 S84 内。
标了它，下游才不会把「PDF 只有 35 页」当成「这节课只讲了 35 页」。

渲染后端
--------
优先 PyMuPDF（``fitz``）；不可用时回退 ``pdftoppm``（Poppler）。
文本提取同源。**纯图页**（正文全在图像里）标 ``isPureImagePage: true``——
L08 的 p8/p11/p13/p20/p21/p28–p31/p33 都是这类页，必须以页面图像为准。
判定不能只看单页字符数：p8/p11/p20 的文本是 60 字符的**页眉页脚**
（``KNOWLEDGE REPRESENTATION`` / ``COGNITIVE PSYCHOLOGY / 认知心理学`` / ``03``），
p13 只有标题 ``Formation of memory``。所以先算全文档的「版式家具」再判（见 :func:`extract_pages`）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

PDF_DATE = re.compile(r"D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?([Zz]|[+-]\d{2}'?\d{2}'?)?")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def parse_pdf_date(value: str | None) -> str | None:
    """PDF 元数据日期 ``D:20261012212900+08'00'`` -> ISO 8601。"""
    if not value:
        return None
    m = PDF_DATE.search(value)
    if not m:
        return None
    y, mo, d, hh, mm, ss, tz = m.groups()
    tz = tz or ""
    if tz in ("Z", "z"):
        tz = "+00:00"
    elif tz:
        tz = tz.replace("'", "")
        tz = f"{tz[:3]}:{tz[3:]}" if len(tz) >= 5 else tz
    else:
        tz = ""
    return f"{y}-{mo}-{d}T{hh}:{mm}:{ss or '00'}{tz}"


def open_pdf(path: Path):
    """优先 PyMuPDF。返回 ``(doc, backend)``；都不可用则抛错。"""
    try:
        import fitz  # noqa: F401
        return fitz.open(str(path)), "pymupdf"
    except Exception:                                             # noqa: BLE001
        return None, "pdftoppm"


def render_pages(path: Path, outdir: Path, dpi: int, dry_run: bool) -> tuple[int, str, list[str]]:
    """渲染每一页为 ``PDF pNN.jpg``。返回 ``(page_count, backend, warnings)``。"""
    warnings: list[str] = []
    outdir.mkdir(parents=True, exist_ok=True)
    doc, backend = open_pdf(path)
    if doc is not None:
        n = doc.page_count
        if not dry_run:
            import fitz
            for i in range(n):
                target = outdir / f"PDF p{i + 1:02d}.jpg"
                if target.exists():
                    continue
                pix = doc[i].get_pixmap(dpi=dpi)
                pix.save(str(target))
        doc.close()
        return n, backend, warnings
    if not C.have("pdftoppm"):
        raise RuntimeError("PyMuPDF 与 pdftoppm 都不可用，无法渲染 PDF 页图。"
                           "请安装 PyMuPDF（pip install pymupdf）或 Poppler。")
    if not dry_run:
        C.run(["pdftoppm", "-jpeg", "-r", str(dpi), str(path), str(outdir / "PDF p")])
        for f in sorted(outdir.glob("PDF p*.jpg")):
            pass
    warnings.append("用 pdftoppm 渲染：文件名由 Poppler 生成，可能与 PDF pNN.jpg 命名不完全一致，请核对")
    proc = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    n = 0
    for line in (proc.stdout or "").splitlines():
        if line.startswith("Pages:"):
            n = int(line.split(":", 1)[1].strip())
    return n, "pdftoppm", warnings


def extract_pages(path: Path) -> tuple[list[dict], dict, list[str]]:
    """逐页提取文本与元数据，并判定**纯图页**。返回 ``(pages, meta, warnings)``。

    纯图页判定（实测校准，必须整份文档一起看，不能只看单页字符数）：

    1. 先算**版式家具**——出现在 ``max(3, 20% 页数)`` 个以上页面上的行
       （如 L08 的 ``KNOWLEDGE REPRESENTATION``、``COGNITIVE PSYCHOLOGY / 认知心理学``）
       以及纯数字行（页脚小节号）。
    2. 去掉家具后：**没有剩余行**，或**只剩一行且短于 40 字符**（只有标题、正文全在图里）
       -> ``isPureImagePage: true``。

    为什么不能只看 ``len(text) < 5``：L08 的 p8/p11/p20 文本是
    ``KNOWLEDGE REPRESENTATION / COGNITIVE PSYCHOLOGY / 认知心理学 / 03`` 共 60 字符，
    看着「有文本」，其实全是页眉页脚；p13 只有标题 ``Formation of memory``。
    只看字符数会把 10 个纯图页全部漏掉（实测）。
    """
    warnings: list[str] = []
    pages: list[dict] = []
    meta: dict = {}
    try:
        import fitz
    except Exception:                                             # noqa: BLE001
        warnings.append("PyMuPDF 不可用：跳过逐页文本提取（isPureImagePage 无法判定）")
        return pages, meta, warnings

    doc = fitz.open(str(path))
    md = doc.metadata or {}
    meta = {
        "pageCount": doc.page_count,
        "author": md.get("author") or None,
        "createdAt": parse_pdf_date(md.get("creationDate")),
        "title": md.get("title") or None,
    }

    raw_lines: list[list[str]] = []
    for i in range(doc.page_count):
        text = (doc[i].get_text("text") or "").strip()
        raw_lines.append([ln.strip() for ln in text.splitlines() if ln.strip()])
    doc.close()

    counts: dict[str, int] = {}
    for lines in raw_lines:
        for ln in set(lines):
            counts[ln] = counts.get(ln, 0) + 1
    threshold = max(3, int(len(raw_lines) * 0.20))
    furniture = {ln for ln, c in counts.items() if c >= threshold}
    numeric = re.compile(r"^[\d\s\-–—.、/()（）]+$")

    for i, lines in enumerate(raw_lines, 1):
        remainder = [ln for ln in lines
                     if ln not in furniture and not numeric.match(ln) and len(ln) >= 2]
        is_pure = (not remainder) or (len(remainder) == 1 and len(remainder[0]) < 40)
        pages.append({
            "pdfPage": i,
            "text": "\n".join(lines),
            "lineCount": len(lines),
            "isPureImagePage": is_pure,
            "firstLine": remainder[0] if remainder else (lines[0] if lines else None),
        })
    if furniture:
        warnings.append(f"版式家具（出现在 {threshold}+ 页，判定纯图页时已剔除）："
                        + "、".join(sorted(furniture)[:6]))
    return pages, meta, warnings


def heuristic_title(page: dict) -> str | None:
    """用页首文本行做标题**启发式**。人工可用 ``--title-map`` 覆盖。"""
    first = page.get("firstLine")
    if not first:
        return None
    return first[:80]


def load_map(path: str | None) -> dict:
    if not path:
        return {}
    return C.read_json(Path(path))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf.py",
        description="教师 PDF -> 渲染页图（pdf-render 链），产出 raw/。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="pdfPage 一律是 PDF 阅读器页序，不是幻灯片印刷页脚；页脚读数走 --page-no-map。",
    )
    p.add_argument("--pdf", required=True, help="PDF 文件路径")
    p.add_argument("--out", required=True, help="工作目录（产物落 <out>/raw/）")
    p.add_argument("--dpi", type=int, default=150, help="渲染 DPI（默认 150）")
    p.add_argument("--no-render", action="store_true", help="只提取元数据与文本，不渲染页图")
    p.add_argument("--is-selection", action="store_true",
                   help="这份 PDF 是**选页**而非课件全部（L08 的 S84 即是）")

    p.add_argument("--course-id", default=None, help="course id（pdf-only 时通常写 unknown）")
    p.add_argument("--course-name", default=None, help="课程名")
    p.add_argument("--course-code", default=None, help="课程代码，如 HLT3202G")
    p.add_argument("--pdf-label", default=None, help="PDF 在 Source Register 里的标签，如「第8次课.pdf」")

    p.add_argument("--mark-content-type", action="append", default=None, metavar="N=TYPE",
                   help="人工标记第 N 个阅读页的类型，如 13=video（L08 p13 是未导出的嵌入视频）")
    p.add_argument("--flag-page", action="append", default=None, metavar="N=ISSUE",
                   help="人工登记某页的文本提取问题，如 14=\"文本顺序错乱：两方框标题与正文交错\"")
    p.add_argument("--title-map", default=None, help="每页标题 JSON：{\"9\": \"Three forms of long-term memory\"}")
    p.add_argument("--footer-unit", default=None, help="默认栏目条 / 单元名，如 \"EYE TRACKING\"")
    p.add_argument("--page-no-scope", choices=list(C.PAGE_NO_SCOPES), default=None,
                   help="页脚编号作用域：section（L08 实测）| page | none")
    p.add_argument("--page-no-map", default=None,
                   help="页脚对照表 JSON：{\"24\": {\"expected\":\"42\",\"fullOcr\":\"42\",\"footerOcr\":\"42\","
                        "\"status\":\"anomalous\",\"note\":\"...\"}}，键是 pdfPage")
    p.add_argument("--note", default=None, help="pdf.note，如「p13 紫色区域是未导出的嵌入视频」")
    p.add_argument("--file-prefix", default=None,
                   help="写入 slides[].file 的路径前缀（归档目录），默认写 raw 内相对路径")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    return p


def parse_pairs(pairs: list[str] | None, what: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"{what} 需要 N=值 形式，收到：{item}")
        k, v = item.split("=", 1)
        out[int(k)] = v
    return out


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        print(f"找不到 PDF：{pdf_path}", file=sys.stderr)
        return 2
    work = Path(args.out).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    pages, meta, warns = extract_pages(pdf_path)
    warnings += warns
    outdir = C.raw_dir(work) / "pdf-pages"

    if args.no_render:
        page_count = meta.get("pageCount") or len(pages)
        backend = "none"
    else:
        page_count, backend, warns = render_pages(pdf_path, outdir, args.dpi, args.dry_run)
        warnings += warns
    if not page_count:
        page_count = meta.get("pageCount") or len(pages)
    if page_count and pages and page_count != len(pages):
        warnings.append(f"渲染页数 {page_count} 与文本提取页数 {len(pages)} 不一致，请核对")

    marks = parse_pairs(args.mark_content_type, "--mark-content-type")
    for v in marks.values():
        if v not in C.CONTENT_TYPES:
            print(f"未知 contentType：{v}（允许：{', '.join(C.CONTENT_TYPES)}）", file=sys.stderr)
            return 2
    flags = parse_pairs(args.flag_page, "--flag-page")
    titles = load_map(args.title_map)
    pageno = load_map(args.page_no_map)

    slides = []
    pure = []
    for i in range(1, page_count + 1):
        page = pages[i - 1] if i - 1 < len(pages) else {}
        rel = f"pdf-pages/PDF p{i:02d}.jpg"
        file_str = f"{args.file_prefix.rstrip('/')}/{Path(rel).name}" if args.file_prefix else f"raw/{rel}"
        title = titles.get(str(i)) or titles.get(i) or heuristic_title(page)
        ct = marks.get(i, "slide")
        issue = flags.get(i)
        entry = C.make_slide(len(slides) + 1, file_str, "pdf-render",
                             projections=[], pdfPage=i, title=title, contentType=ct)
        if page.get("isPureImagePage"):
            entry["isPureImagePage"] = True
            pure.append(i)
        if issue:
            entry["textExtractionIssue"] = issue
        # 页脚对照表（人工登记；本脚本不猜）
        pn = pageno.get(str(i)) or pageno.get(i) or {}
        unit = pn.get("footerUnit") or args.footer_unit
        scope = pn.get("pageNoScope") or args.page_no_scope
        if unit:
            entry["footerUnit"] = unit
        if scope:
            entry["pageNoScope"] = scope
        for src_key, dst_key in (("expected", "pageNoExpected"), ("fullOcr", "pageNoFullOcr"),
                                 ("footerOcr", "pageNoFooterOcr")):
            if pn.get(src_key) is not None:
                entry[dst_key] = str(pn[src_key])
        norm = C.normalize_page_no(pn.get("footerOcr") or pn.get("fullOcr") or pn.get("expected"))
        if norm:
            entry["pageNoNormalized"] = norm
        if pn.get("status"):
            if pn["status"] not in C.PAGE_NO_STATUSES:
                print(f"未知 pageNoStatus：{pn['status']}（允许：{', '.join(C.PAGE_NO_STATUSES)}）",
                      file=sys.stderr)
                return 2
            entry["pageNoStatus"] = pn["status"]
        if pn.get("note"):
            entry["pageNoNote"] = pn["note"]
        slides.append(entry)

    if pure:
        warnings.append(f"纯图页（文本提取为空，必须以页面图像为准）：{len(pure)} 页 -> "
                        f"p{', p'.join(str(x) for x in pure[:12])}{'…' if len(pure) > 12 else ''}")
    if marks:
        warnings.append("人工标记 contentType：" + "、".join(f"p{k}={v}" for k, v in sorted(marks.items())))
    if args.is_selection:
        warnings.append("这份 PDF 是**选页**（isSelection=true）：课堂还放过 S84 之外的页，"
                        "不能把 PDF 页数当作本节课的页数上限")
    if not pageno:
        warnings.append("未提供 --page-no-map：页脚读数全部留空，pageNoStatus 将由 build_manifest 判为 missing")
    else:
        warnings.append("页脚不是页码而是小节/版式编号：跨页重复（L08 p26–p31/p33 都是 10）是正常的，"
                        "只有明显偏离相邻页序列才判 anomalous")
    warnings.append("PDF 渲染页图（pdf-render）与平台快照轨是两条独立证据链；放映时间码只能由人工 "
                    "build_manifest.py --projection-map 显式给出")

    if not args.dry_run:
        pdf_block = {
            "file": args.pdf_label or pdf_path.name,
            "pageCount": page_count,
            "sha256": sha256_of(pdf_path),
            "author": meta.get("author"),
            "createdAt": meta.get("createdAt"),
            "isSelection": bool(args.is_selection),
            "textExtractionIssues": ([f"纯图页文本提取为空：p{', p'.join(str(x) for x in pure)}"] if pure else [])
                                    + [f"p{k}：{v}" for k, v in sorted(flags.items())],
            "note": args.note,
        }
        C.write_pdf_block(work, pdf_block)
        C.write_pdf_slides(work, slides)
        C.merge_source(work, "pdf",
                       {"id": str(args.course_id or "unknown"),
                        "name": args.course_name or str(args.course_id or "unknown"),
                        **({"code": args.course_code} if args.course_code else {})},
                       platform_subtitles="none",
                       extra={"renderBackend": backend, "dpi": args.dpi})
        C.add_warnings(work, warnings)

    C.banner("PDF 采集完成" if not args.dry_run else "PDF 采集 dry-run")
    print(f"PDF {pdf_path.name}：{page_count} 个阅读页，后端 {backend}")
    print(f"  raw/pdf.json + raw/pdf/slides.json（{len(slides)} 页，纯图页 {len(pure)}）")
    print(f"  警告 {len(warnings)} 条 -> raw/warnings.json")
    print(f"\n下一步：python {C.SKILL_ROOT / 'scripts' / 'build_manifest.py'} \"{work}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
