#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_notes.py — L2 笔记层生成器（L## / P## / V## / Concept + 索引 + Source Register 行）

输入：`schemas/manifest.schema.json` 的 manifest + 目标库画像（vault-profile.json）
输出：骨架 markdown（**不覆盖已有笔记**）+ 索引更新片段 + Source Register 行

三条硬规则在本脚本里是**机械执行**的，不靠写作时的自觉：

  1. **图片块必须展开**。写 `[!example]-` 折叠会让图默认不可见——有机化学库实测
     509 处嵌入写在折叠块里，72/72 篇 L 笔记打开时可见图数为 0。
     本脚本产出 `[!example]+`（或画像 `callouts.slideBlock`），并在写盘前扫描产物，
     发现 `[!example]-` 就报错。**表格内嵌图片转义竖线 `\\|`。**
  2. **默认内嵌页图**，不用 `#page=`。心理库学习者实测该链接点击无效。
     引用课件页码一律走「PDF 阅读页 + 内嵌页图」，不走页码跳转。
  3. **课件存在 ≠ 课堂讲过**。唯一机器可判依据是 `slides[].projections`：
     **空数组 = 未见对应讲解**，只能写「课件版 / 未见对应讲解」，不能写「已讲授」。

另外按最终 manifest 契约处理的四件事：

  - `projections` 可以是 0/1/多条：同一页可跨节次放映，也可同节次重放（`isReplay`）。
    **不按页号去重**。
  - `contentType` = `video` 的页（L08 p13）要写成「该页是视频，具体讲解口径待视频或转写确认」，
    **不能当成坏页或排版错误**。
  - 页脚是**小节/版式编号，不是唯一页码**（`pageNoScope: section`）：L08 实测 p26–p31 与
    p33 页脚都是 10、p8/p9/p11 都是 03、p17/p18 都是 06，**重复是正常的**，不进待核实。
    只有 `anomalous`（明显偏离相邻页序列，如 p24 页脚 42 而前后页是 08/09）/
    `mismatch` / `missing` 才逐条进待核实，**保留原读数、不猜**。
  - `pdf.isSelection` / `session.usedDeck` / `session.deckCoverageNote` /
    `unarchivedMaterials[]` 必须进待核实清单，否则复核者会以为课件完整。

安全：
  - 默认 **dry-run**（`--dry-run`，也是默认值）：只打印与写临时目录，**不碰知识库**。
  - `--apply` 才写盘；写前跑 Git 门（status → fetch → 比对远端 → 干净才 pull），
    并且只暂存本次目标文件（`stageOnlyTargetFiles` 恒 true）。
  - 已有笔记默认**不覆盖**，改写 `<name>.new.md`；`--force` 才覆盖。

用法：
  python gen_notes.py --manifest <manifest.json> --vault "<vault root>" [--profile <json>]
                      [--dry-run | --apply] [--with-timeline] [--out-dir <tmp>]
                      [--sample] [--validate-manifest]

退出码：0 成功；2 参数问题；3 写盘/校验失败；4 Git 门拦住。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(HERE, "..", "templates")

# 页脚状态 → 是否需要进待核实清单（`verified` 之外全要）
#
# ⚠️ 页脚**不是唯一页码，是小节/版式编号**（`pageNoScope: section`）：
#    心理库 L08 实测 p26–p31 与 p33 页脚都是 10（7 页共享一个小节号）、
#    p8/p9/p11 都是 03、p17/p18 都是 06。**重复是正常的，不进待核实。**
#    只有 anomalous（明显偏离相邻页序列）/ mismatch / missing 才要人工判断。
FOOTER_NEEDS_REVIEW = {"anomalous", "mismatch", "missing"}

FOOTER_STATUS_ZH = {
    "verified": "verified（正常）",
    "anomalous": "anomalous（异常，保留原读数）",
    "mismatch": "mismatch（两路读数不一致）",
    "missing": "missing（无页脚数字）",
    "not-applicable": "not-applicable（无页码体系）",
}

CONTENT_TYPE_ZH = {
    "slide": "slide（课件页）",
    "video": "video（嵌入视频占位）",
    "activity": "activity（课堂活动/练习）",
    "desktop": "desktop（教师桌面/应用窗口，非课件页）",
    "unknown": "unknown（未判定）",
}


# ═══════════════════════════════════════════════════════════════════════════
# 小工具
# ═══════════════════════════════════════════════════════════════════════════

def _read_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _template(name: str) -> str:
    return _read_text(os.path.join(TEMPLATE_DIR, name))


def _fill(tpl: str, mapping: Dict[str, str]) -> str:
    """替换 `{{key}}`，并去掉模板作者注 `{#- … -#}`。

    **未提供的占位符原样保留**——宁可在产物里看到 `{{x}}`，
    也不要静默留空让读者以为那一节本来就空（`audit_output` 会把它们报成红）。
    """
    # 去掉模板作者注 `{#- … -#}`，并吃掉它留下的空行——
    # 否则 frontmatter 开头的 `---` 后面会多一个空行。
    tpl = re.sub(r"[ \t]*\{#-?.*?-?#\}[ \t]*\r?\n?", "", tpl, flags=re.S)

    def sub(m: re.Match) -> str:
        k = m.group(1)
        return mapping.get(k, m.group(0))
    return re.sub(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}", sub, tpl)


def _mmss(sec: Optional[float]) -> str:
    if sec is None:
        return "—"
    s = int(round(sec))
    return f"{s // 60:02d}:{s % 60:02d}"


def _page_label(sl: Dict[str, Any]) -> str:
    """页的显示标签：优先 PDF 阅读页，其次清单序号。"""
    if sl.get("pdfPage") is not None:
        return f"p{sl['pdfPage']}"
    return f"#{sl['index']}"


def _norm_page_sort(sl: Dict[str, Any]) -> Tuple[int, int]:
    return (0, sl["pdfPage"]) if sl.get("pdfPage") is not None else (1, sl["index"])


# ═══════════════════════════════════════════════════════════════════════════
# 图片嵌入：三条硬规则在这里落地
# ═══════════════════════════════════════════════════════════════════════════

class Embedder:
    """按画像产出图片嵌入。

    硬规则：
      - `escapePipeInTables` 恒 true → 表格里写 `\\|`
      - `preferEmbeddedPageImage` 恒 true → 产出 `![[页图]]`，**绝不产出 `#page=`**
      - 宽度：画像 `lectureWidth` 为 null（有机化学库实测如此）时**不写宽度**，
        不替它编一个数。
    """

    def __init__(self, profile: Dict[str, Any]):
        images = profile.get("images", {})
        self.syntax = images.get("embedSyntax") or "![[{path}|{width}]]"
        self.lecture_width = images.get("lectureWidth")
        self.deck_width = images.get("deckWidth") or self.lecture_width
        self.prefer_embedded = images.get("preferEmbeddedPageImage", True)

    def embed(self, path: str, width: Optional[int] = None,
              in_table: bool = False) -> str:
        w = width if width is not None else self.lecture_width
        if w:
            out = f"![[{path}|{w}]]"
        else:
            out = f"![[{path}]]"
        if in_table:
            # 表格内嵌图片必须转义竖线，否则 `|宽度` 会被当成新的表格列。
            out = out.replace("|", "\\|")
        return out

    def page_link_refused(self) -> str:
        return ("（本 skill 不产出 `#page=` 跳转——心理库学习者实测点击无效；"
                "引用页码请用 PDF 阅读页 + 内嵌页图）")


# ═══════════════════════════════════════════════════════════════════════════
# 放映记录 / 覆盖判定
# ═══════════════════════════════════════════════════════════════════════════

def projections_of(sl: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sl.get("projections") or []


def is_taught(sl: Dict[str, Any]) -> bool:
    """**唯一机器可判的「已讲授」依据**：projections 非空。"""
    return len(projections_of(sl)) > 0


def project_rows(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 slides[].projections 摊平成行。同一页多条 → 多行。**不去重**。"""
    sessions = manifest["source"]["sessions"]
    rows: List[Dict[str, Any]] = []
    for sl in sorted(manifest["slides"], key=_norm_page_sort):
        for pj in projections_of(sl):
            si = pj["sessionIndex"]
            sess = sessions[si - 1] if 0 < si <= len(sessions) else {}
            rows.append({
                "sessionIndex": si,
                "sessionLabel": sess.get("label", f"节次 {si}"),
                "startSec": pj.get("startSec"),
                "endSec": pj.get("endSec"),
                "mmss": pj.get("startMmss") or _mmss(pj.get("startSec")),
                "isReplay": bool(pj.get("isReplay")),
                "timecodeSource": pj.get("timecodeSource"),
                "rawTimecode": pj.get("rawTimecode"),
                "slide": sl,
                "page": _page_label(sl),
                "title": sl.get("title") or "—",
                "contentType": sl.get("contentType") or "unknown",
                "pdfPage": sl.get("pdfPage"),
            })
    return rows


def coverage_stats(manifest: Dict[str, Any]) -> Dict[str, Any]:
    slides = sorted(manifest["slides"], key=_norm_page_sort)
    taught = [s for s in slides if is_taught(s)]
    not_taught = [s for s in slides if not is_taught(s)]
    nonslide = [s for s in slides
                if (s.get("contentType") or "slide") in ("video", "activity", "desktop")]
    return {
        "total": len(slides),
        "taught": taught,
        "notTaught": not_taught,
        "nonSlide": nonslide,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 证据边界：必须进待核实清单的事实
# ═══════════════════════════════════════════════════════════════════════════

def collect_to_verify(manifest: Dict[str, Any]) -> List[Dict[str, str]]:
    """把「需要人工判断」的事实逐条收齐。每条的 `where` 指明它来自哪一页/哪一节。

    这里**不产生结论**，只搬运 manifest 里已有的事实——不猜。
    """
    out: List[Dict[str, str]] = []
    slides = sorted(manifest["slides"], key=_norm_page_sort)
    pdf = manifest.get("pdf") or {}
    sessions = manifest["source"]["sessions"]

    # ── 页脚异常 ──────────────────────────────────────────────────────────
    for sl in slides:
        st = sl.get("pageNoStatus")
        if st in FOOTER_NEEDS_REVIEW:
            raw = sl.get("pageNoFooterOcr") or sl.get("pageNoFullOcr") or "无读数"
            norm = sl.get("pageNoNormalized")
            exp = sl.get("pageNoExpected")
            bits = [f"原始读数「{raw}」"]
            if norm:
                bits.append(f"归一化 {norm}")
            if exp and norm and exp != norm:
                bits.append(f"该小节应印 {exp}")
            out.append({
                "where": f"{_page_label(sl)}（{sl.get('title') or '—'}）",
                "what": f"页脚状态 `{st}`：{'；'.join(bits)}",
                "why": (sl.get("pageNoNote")
                        or "manifest 未给 pageNoNote；需人工核对原始页图"),
            })
        if sl.get("isPureImagePage"):
            out.append({
                "where": _page_label(sl),
                "what": "纯图页：文本提取为空",
                "why": "必须以页面图像为准，不能按上下文补齐",
            })
        if sl.get("textExtractionIssue"):
            out.append({
                "where": _page_label(sl),
                "what": f"文本提取问题：{sl['textExtractionIssue']}",
                "why": "文本顺序/内容不可直接采信",
            })
        ct = sl.get("contentType")
        if ct == "video":
            out.append({
                "where": _page_label(sl),
                "what": "该页是**嵌入视频**（非课件排版页）",
                "why": ("PDF 里的区域是未导出的视频占位，不是坏页或排版错误；"
                        "视频内容与具体讲解口径待视频或转写确认"),
            })
        elif ct == "desktop":
            out.append({
                "where": _page_label(sl),
                "what": "该帧是教师桌面/应用窗口截图，不是课件页",
                "why": "不能当成课件内容引用",
            })

    # ── 未见对应讲解 ──────────────────────────────────────────────────────
    nt = [s for s in slides if not is_taught(s)]
    if nt:
        out.append({
            "where": "、".join(_page_label(s) for s in nt),
            "what": f"{len(nt)} 页 `projections` 为空 = 未见对应讲解",
            "why": "课件存在 ≠ 课堂讲过；这些页只能写「课件版 / 未见对应讲解」",
        })

    # ── PDF 是选页 ────────────────────────────────────────────────────────
    if pdf.get("isSelection"):
        out.append({
            "where": f"课件文件 {pdf.get('file') or '（未记录文件名）'}",
            "what": "`pdf.isSelection = true`：这份 PDF 是**选页**，不是课件全部",
            "why": (pdf.get("note")
                    or "课堂可能还放映了本 PDF 之外的页；需取得完整课件后再补录"),
        })
    if pdf.get("note"):
        out.append({
            "where": "课件文件",
            "what": f"课件备注：{pdf['note']}",
            "why": "影响「课件是否完整」的判断",
        })

    # ── 节次实际使用的课件 / 覆盖范围 ─────────────────────────────────────
    for i, sess in enumerate(sessions, 1):
        used = sess.get("usedDeck")
        cov = sess.get("deckCoverageNote")
        if used or cov:
            out.append({
                "where": f"节次 {i}「{sess.get('label', '')}」",
                "what": (f"该节次实际使用的课件：`{used}`" if used else "该节次课件使用情况")
                        + (f"；覆盖说明：{cov}" if cov else ""),
                "why": ("若与内容基准 PDF 不是同一份，则本节次有部分内容无法在现有索引中定位；"
                        "复核者会误以为课件完整"),
            })
        t = sess.get("transcript") or {}
        for m in (t.get("knownMishearings") or []):
            out.append({
                "where": f"节次 {i} 转写",
                "what": f"已知误听：{m}",
                "why": "这些词**不能作为逐字教师引语**",
            })
        if t.get("qualityNote"):
            out.append({
                "where": f"节次 {i} 转写",
                "what": f"转写质量备注：{t['qualityNote']}",
                "why": "限制可引用的范围",
            })
        if t and not t.get("diarized"):
            pass
        if t.get("speakerLabelsAreInferred"):
            out.append({
                "where": f"节次 {i} 转写",
                "what": "说话人标签由自动分离得到（`speakerLabelsAreInferred = true`）",
                "why": "自动说话人标签是**推断**，不是身份；引用前须核对原始音频",
            })

    # ── 未归档材料 ────────────────────────────────────────────────────────
    for m in (manifest.get("unarchivedMaterials") or []):
        out.append({
            "where": "未归档材料",
            "what": m["what"],
            "why": (m.get("impact") or "尚未归档") + f"｜依据：{m['evidence']}",
        })

    # ── 平台产物只作交叉校验 ──────────────────────────────────────────────
    for i, sess in enumerate(sessions, 1):
        for a in (sess.get("platformArtifacts") or []):
            out.append({
                "where": f"节次 {i} 平台产物",
                "what": f"`{a['kind']}`：{a['file']}"
                        + (f"（记录数 {a['recordCount']}）" if a.get("recordCount") else ""),
                "why": ("平台自带 AI 产物**只作交叉校验、不直接引用**——"
                        "中英混排识别质量通常低于本地转写。"
                        + (f"备注：{a['note']}" if a.get("note") else "")),
            })

    # ── 媒体归档状态 ──────────────────────────────────────────────────────
    for i, sess in enumerate(sessions, 1):
        md = sess.get("media") or {}
        if md and not md.get("archived"):
            out.append({
                "where": f"节次 {i} 原始媒体",
                "what": (md.get("note") or "原始媒体未长期归档"),
                "why": "Source Register 只登记来源与「原始媒体未归档」状态，**不建失效链接**",
            })
    return out


def to_verify_lines(items: List[Dict[str, str]], checked: bool = True) -> str:
    if not items:
        return "- [x] 本清单为空（manifest 未报出需要人工判断的事实）"
    box = "- [ ]" if checked else "-"
    out = []
    for it in items:
        out.append(f"{box} **{it['where']}** — {it['what']}")
        out.append(f"      ↳ 为什么必须留痕：{it['why']}")
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════════════════════
# L 笔记
# ═══════════════════════════════════════════════════════════════════════════

def build_lecture(manifest: Dict[str, Any], profile: Dict[str, Any],
                  template: str) -> str:
    src = manifest["source"]
    course = src["course"]
    sessions = src["sessions"]
    slides = sorted(manifest["slides"], key=_norm_page_sort)
    stats = coverage_stats(manifest)
    emb = Embedder(profile)

    lecture_id = next((s.get("lectureId") for s in sessions if s.get("lectureId")), None) \
        or "L??"
    class_dates = sorted({s["classDate"] for s in sessions if s.get("classDate")})
    class_date = class_dates[0] if len(class_dates) == 1 else (class_dates or ["未确认"])
    class_date_str = class_date if isinstance(class_date, str) else "；".join(class_date)

    # ── frontmatter ───────────────────────────────────────────────────────
    fm_key = profile.get("frontmatter", {}).get("frontmatterKey") or "type"
    # 画像里没有单独的 frontmatterKey 字段（schema 冻结），按 required 推断：
    req = profile.get("frontmatter", {}).get("required") or []
    if "note_type" in req:
        fm_key = "note_type"
    elif "type" in req:
        fm_key = "type"

    source_lines = []
    for s in sessions:
        t = s.get("transcript") or {}
        if t.get("file"):
            source_lines.append(f'  - "[[{t["file"]}|{s["label"]} 转写]]"')
    if (manifest.get("pdf") or {}).get("file"):
        source_lines.append(f'  - "[[{manifest["pdf"]["file"]}|教师课件 PDF]]"')
    if not source_lines:
        source_lines.append("  - （待补：manifest 未给出可链接的来源文件）")

    tags = [f"  - {course.get('code', '').lower() or 'course'}",
            f"  - {lecture_id.lower()}"]
    for tg in (manifest.get("tags") or []):
        tags.append(f"  - {tg}")

    # ── 节次行 ────────────────────────────────────────────────────────────
    sess_bits = []
    for i, s in enumerate(sessions, 1):
        bits = [f"**{i}. {s.get('label', '')}**"]
        if s.get("platformSessionId"):
            bits.append(f"平台 id `{s['platformSessionId']}`")
        if s.get("letiNumber") is not None:
            bits.append(f"第 {s['letiNumber']} 节")
        if s.get("partOfSplit"):
            bits.append(f"第 {s['partOfSplit']} 段")
        if s.get("durationSec"):
            bits.append(f"时长 {_mmss(s['durationSec'])}")
        if s.get("classDate"):
            bits.append(s["classDate"])
        sess_bits.append("；".join(bits))
    sessions_line = "<br>".join(sess_bits) if sess_bits else "（manifest 未给出节次）"

    deck_basis_line = "；".join(filter(None, [
        f"教师课件 `{(manifest.get('pdf') or {}).get('file')}`"
        if (manifest.get("pdf") or {}).get("file") else None,
        f"共 {len(slides)} 个 PDF 阅读页" if slides else None,
        "**该 PDF 是选页，不是课件全部**" if (manifest.get("pdf") or {}).get("isSelection")
        else None,
    ])) or "（manifest 未给出课件基准）"

    ev_tags = profile.get("evidenceTags") or []
    evidence_tags_line = ("　".join(f"**[{t}]**" for t in ev_tags)
                          if ev_tags else "（画像未给出该库的证据标记集——需人工确认）")
    # 「有没有课堂口述证据」看的是**转写文件是否存在**，不是有没有把分段塞进 manifest。
    # 分段可以只留在转写文件里（L08 实测：manifest 只登记 file/模型/段数，不带 segments）。
    has_transcript = any((s.get("transcript") or {}).get("file") for s in sessions)
    t_models = sorted({(s.get("transcript") or {}).get("model")
                       for s in sessions if (s.get("transcript") or {}).get("model")})
    evidence_caveat_line = (
        "> 本笔记的课堂引语**依据机器转写**（"
        + ("、".join(t_models) if t_models else "模型未记录")
        + "），**未经音频逐句核对**；说话人标签是推断、不是身份。"
        if has_transcript else
        "> **本笔记没有任何课堂口述证据**（manifest 未提供转写）——"
        "全部内容按课件材料整理，课堂状态为「课件版」。"
    )

    # ── 中心问题 ──────────────────────────────────────────────────────────
    import_pages = [s for s in slides
                    if s.get("title") and re.search(r"(导入|introduction|intro|checkpoint)",
                                                    s["title"], re.I)]
    if import_pages:
        cq = []
        for s in import_pages:
            cq.append(f"**{_page_label(s)} — {s['title']}**"
                      + (" [PPT原文]" if "PPT原文" in ev_tags else ""))
            cq.append("")
            cq.append("| 前课内容 | 本课追问（原文） |")
            cq.append("|---|---|")
            cq.append("| （待补：按页图与课件原文填写） | **（待补）** |")
            cq.append("")
        central_questions = "\n".join(cq)
    else:
        central_questions = ("（本课件未识别到标题含 Checkpoint / 导入 的页面。"
                             "**这是核查结果，不是漏登**——请人工确认后再补。）")

    self_questions = ("1. （待补：本课要回答的三个问题，供复习自查）\n"
                      "2. \n3. ")

    # ── 覆盖表 ────────────────────────────────────────────────────────────
    pages_taught = "、".join(_page_label(s) for s in stats["taught"]) or "—"
    pages_nt = "、".join(_page_label(s) for s in stats["notTaught"]) or "—"
    pages_ns = "、".join(_page_label(s) for s in stats["nonSlide"]) or "—"

    cov_notes = []
    if (manifest.get("pdf") or {}).get("isSelection"):
        cov_notes.append(
            "> [!warning] 这份 PDF 是**选页**\n"
            "> `pdf.isSelection = true`。课堂可能还放映了本 PDF 之外的页，"
            "它们无法在现有索引中定位；**取得完整课件前，不要把本索引当作课件全集**。")
    basis_deck = (manifest.get("pdf") or {}).get("file") or ""
    for i, s in enumerate(sessions, 1):
        used = s.get("usedDeck")
        cov = s.get("deckCoverageNote")
        # 只在「实际用的课件不是内容基准课件」时才警告。
        # 若 usedDeck 就是基准 PDF，说明该节次本来就在基准课件范围内——
        # 此时 deckCoverageNote 描述的是覆盖情况（含 S84 之外的页），不是「不在范围内」。
        if used and basis_deck and os.path.basename(used) not in basis_deck:
            cov_notes.append(
                f"> [!warning] 节次 {i}「{s.get('label', '')}」有部分内容**不在内容基准课件范围内**\n"
                f"> 实际使用课件：`{used}`；覆盖说明：{cov or '未记录'}。\n"
                "> 这些时间段的页面**无法在现有课件索引中定位**——"
                "不写明，复核者会以为课件完整。")
        elif cov:
            cov_notes.append(
                f"> [!note] 节次 {i}「{s.get('label', '')}」的课件覆盖情况\n"
                f"> {cov}")
    coverage_notes = "\n\n".join(cov_notes)

    unarch = manifest.get("unarchivedMaterials") or []
    if unarch:
        blocks = ["> [!warning] 课堂用到但**尚未归档**的材料"]
        for m in unarch:
            blocks.append(f"> - **{m['what']}**")
            blocks.append(f">   - 依据：{m['evidence']}")
            if m.get("impact"):
                blocks.append(f">   - 对结论的限制：{m['impact']}")
        unarchived_callouts = "\n".join(blocks)
    else:
        unarchived_callouts = ""

    # ── 正文骨架：按概念分组，图片块展开 ──────────────────────────────────
    slide_callout = (profile.get("callouts") or {}).get("slideBlock") or "[!example]"
    slide_callout = _force_expanded(slide_callout)

    body_parts: List[str] = []
    # 用放映记录切成"已讲授"的段，按节次与时间排序
    rows = project_rows(manifest)
    taught_rows = [r for r in rows]
    if taught_rows:
        body_parts.append("<!-- 下列小节按**课堂放映顺序**排列，不是按 PDF 页序。")
        body_parts.append("     同一页出现两次是正常的（跨节次放映或同节次重放），不要合并。 -->")
        body_parts.append("")
        for r in taught_rows:
            sl = r["slide"]
            img = emb.embed(sl["file"])
            replay = "（重放）" if r["isReplay"] else ""
            body_parts.append(
                f"### {r['page']} — {r['title']}　"
                f"[{r['sessionLabel']} {r['mmss']}{replay}]")
            body_parts.append("")
            body_parts.append(
                f"> {slide_callout} {r['sessionLabel']} {r['mmss']}{replay}"
                f"｜PDF 阅读页 {r['pdfPage'] if r['pdfPage'] is not None else '—'}"
                f"：{r['title']}")
            body_parts.append(f"> {img}")
            body_parts.append("")
            if sl.get("contentType") == "video":
                body_parts.append(
                    "> [!note] 这一页是**视频**，不是排版页\n"
                    "> PDF 里的区域是**未导出的视频占位**，不是坏页或排版错误。\n"
                    "> 视频内容与具体讲解口径**待视频或转写确认**。")
                body_parts.append("")
            body_parts.append("（待补：本页在讲什么、关键概念与推断边界。"
                              "一段只讲一个重点。）")
            body_parts.append("")
    else:
        body_parts.append("（manifest 的 `slides[].projections` 全为空："
                          "**本课没有可用的课堂放映记录**。")
        body_parts.append("本笔记只能整理为「**课件版**」，不得写「已讲授」。）")
        body_parts.append("")
        for sl in slides[:1]:
            body_parts.append(f"> {slide_callout} 课件页 {_page_label(sl)}：{sl.get('title') or '—'}")
            body_parts.append(f"> {emb.embed(sl['file'])}")
            body_parts.append("")
    body = "\n".join(body_parts)

    key_points = "\n".join(f"{i}. " for i in range(1, 5))

    # ── 页脚异常 ──────────────────────────────────────────────────────────
    foot_bits: List[str] = []
    for sl in slides:
        st = sl.get("pageNoStatus")
        if st in FOOTER_NEEDS_REVIEW:
            raw = sl.get("pageNoFooterOcr") or sl.get("pageNoFullOcr") or "无读数"
            # 只有 `pageNoExpected` 与实测读数**不一致**时才说「该小节应印 N」；
            # 两者相同（如 p24 实测 42 且 manifest 也把 expected 记作 42）时说这句话
            # 会自相矛盾——读者会以为这页没问题。
            exp = sl.get("pageNoExpected")
            norm = sl.get("pageNoNormalized")
            exp_txt = ""
            if exp and norm and exp != norm:
                exp_txt = f"，该小节应印 {exp}"
            foot_bits.append(
                f"- **{_page_label(sl)}**（{sl.get('title') or '—'}）："
                f"{FOOTER_STATUS_ZH.get(st, st)}，原始读数「{raw}」{exp_txt}"
                + (f"\n  - 判断依据：{sl['pageNoNote']}" if sl.get("pageNoNote") else ""))
    if foot_bits:
        footer_anomalies = ("\n".join(foot_bits)
                            + "\n\n> 这些页的页脚**保留原始读数、不静默改名**（目标库 AGENTS.md 要求）；"
                              "引用一律以 PDF 阅读页为准。")
    else:
        footer_anomalies = "- 无（本课件的页脚状态全部为 `verified`）"

    # ── 待核实清单 ────────────────────────────────────────────────────────
    tvs = collect_to_verify(manifest)
    to_verify = to_verify_lines(tvs)
    for w in (manifest.get("warnings") or []):
        to_verify += f"\n- [ ] **manifest.warnings** — {w}"

    active_recall = ("1. **定义 Define：** \n"
                     "2. **解释 Explain：** \n"
                     "3. **评价或应用 Evaluate / apply：** ")

    connections = "\n".join([
        f"- [[{profile.get('dirs', {}).get('sourceRegister') or '09 参考资料/Source Register'}"
        f"|资料登记表 Source Register]]",
        "- （待补：上一节 / 下一节 / P 课件索引 / 概念索引 的回链）",
    ])

    mapping = {
        "note_type_key": fm_key,
        "course_code": course.get("code") or "（未记录课程代码）",
        "lecture_id": lecture_id,
        "class_date": class_date_str,
        "class_date_note": "；".join(
            f"节次 {i} 材料日期 {s.get('classDate')}" for i, s in enumerate(sessions, 1)),
        "material_date": src["capturedAt"][:10],
        "status": "integrated" if has_transcript else "slide-based",
        "source_lines": "\n".join(source_lines),
        "tag_lines": "\n".join(tags),
        "title_zh": "（待补：中文主题）",
        "title_en": "（待补 English Topic）",
        "one_line_summary": "（待补：2–4 句概括本节要解决的核心问题与主线）",
        "sessions_line": sessions_line,
        "deck_basis_line": deck_basis_line,
        "evidence_tags_line": evidence_tags_line,
        "evidence_caveat_line": evidence_caveat_line,
        "central_questions": central_questions,
        "self_questions": self_questions,
        "n_taught": str(len(stats["taught"])),
        "n_not_taught": str(len(stats["notTaught"])),
        "n_nonslide": str(len(stats["nonSlide"])),
        "pages_taught": pages_taught,
        "pages_not_taught": pages_nt,
        "pages_nonslide": pages_ns,
        "coverage_notes": coverage_notes,
        "unarchived_callouts": unarchived_callouts,
        "body": body,
        "key_points": key_points,
        "footer_anomalies": footer_anomalies,
        "to_verify": to_verify,
        "active_recall": active_recall,
        "connections": connections,
    }
    return _fill(template, mapping)


# ═══════════════════════════════════════════════════════════════════════════
# P 课件逐页索引
# ═══════════════════════════════════════════════════════════════════════════

def build_deck(manifest: Dict[str, Any], profile: Dict[str, Any],
               template: str) -> str:
    src = manifest["source"]
    course = src["course"]
    sessions = src["sessions"]
    slides = sorted(manifest["slides"], key=_norm_page_sort)
    pdf = manifest.get("pdf") or {}
    emb = Embedder(profile)

    req = profile.get("frontmatter", {}).get("required") or []
    fm_key = "note_type" if "note_type" in req else "type"
    deck_type_value = {
        "note_type": "slide_overview",
        "type": "ppt-overview",
    }[fm_key]

    lecture_id = next((s.get("lectureId") for s in sessions if s.get("lectureId")), None) \
        or "L??"
    deck_id = lecture_id.replace("L", "P")
    page_count = pdf.get("pageCount") or len(slides)

    source_lines = []
    if pdf.get("file"):
        source_lines.append(f'  - "[[{pdf["file"]}|教师课件 PDF]]"')
    sr = profile.get("dirs", {}).get("sourceRegister")
    if sr:
        source_lines.append(f'  - "[[{sr}|来源登记]]"')
    if not source_lines:
        source_lines.append("  - （待补）")
    tags = [f"  - {course.get('code', '').lower() or 'course'}",
            f"  - {deck_id.lower()}", "  - slides"]

    # ── 范围 callout ──────────────────────────────────────────────────────
    scope: List[str] = []
    if pdf.get("isSelection"):
        scope.append(
            "> [!warning] 这份 PDF 是**选页**，不是课件全部\n"
            "> `pdf.isSelection = true`。课堂还放映了本 PDF 之外的页，"
            "**它们不在本索引内编号**；取得完整课件后再补录。")
    for i, s in enumerate(sessions, 1):
        if s.get("usedDeck") or s.get("deckCoverageNote"):
            scope.append(
                f"> [!warning] 节次 {i}「{s.get('label', '')}」有部分内容不在本课件范围内\n"
                f"> 实际使用课件：`{s.get('usedDeck') or '未记录'}`；"
                f"覆盖说明：`{s.get('deckCoverageNote') or '未记录'}`。\n"
                "> 该时间段的页面**无法在本索引中定位**——不写明，复核者会以为课件完整。")
    scope_callouts = "\n\n".join(scope)

    # ── 内容结构（按 footerUnit 聚合）─────────────────────────────────────
    units: List[Tuple[str, List[Dict[str, Any]]]] = []
    for sl in slides:
        u = sl.get("footerUnit") or "（未记录所属单元）"
        if not units or units[-1][0] != u:
            units.append((u, []))
        units[-1][1].append(sl)
    struct_rows = ["| PDF 阅读页 | 所属单元 | 页数 |", "|---|---|---|"]
    for u, group in units:
        first, last = _page_label(group[0]), _page_label(group[-1])
        span = first if first == last else f"{first}–{last}"
        struct_rows.append(f"| {span} | {u} | {len(group)} |")
    content_structure = "\n".join(struct_rows)

    # ── Checkpoint 核查 ───────────────────────────────────────────────────
    cps = [s for s in slides if s.get("title") and re.search(r"checkpoint", s["title"], re.I)]
    imports = [s for s in slides
               if s.get("title") and re.search(r"(导入|introduction|intro)", s["title"], re.I)]
    if cps:
        checkpoint_note = ("本课件含 Checkpoint 页：" +
                           "、".join(f"**{_page_label(s)}**（{s['title']}）" for s in cps) +
                           "。按目标库 AGENTS.md，Checkpoint 页属重点："
                           "保留总问题与全部子问题的英文原文、中文释义与页码，逐题给回答要点与支持证据。")
    else:
        checkpoint_note = ("本课件 **未出现**标题或标记为 Checkpoint 的页面。"
                           "**这是核查结果，不是漏登**——按目标库 AGENTS.md 记录该结论，"
                           "以免被误认为漏登。")
    if imports:
        checkpoint_note += ("\n\n本课**有**章节/单元导入页：" +
                            "、".join(f"**{_page_label(s)}**（{s['title']}）" for s in imports) +
                            "。其中心问题已登记在 L 笔记的「中心问题」一节，并用于组织内容。")

    # ── 放映记录表 ────────────────────────────────────────────────────────
    sess_labels = [s.get("label", f"节次 {i}") for i, s in enumerate(sessions, 1)]
    head = "| PDF 阅读页 | 页主题 | 内容类型 | " + " | ".join(sess_labels) + " |"
    sep = "|---:|---|---|" + "---|" * len(sess_labels)
    proj_rows = [head, sep]
    rows = project_rows(manifest)
    by_page: Dict[Any, List[Dict[str, Any]]] = {}
    for r in rows:
        by_page.setdefault(r["page"], []).append(r)
    for sl in slides:
        label = _page_label(sl)
        cells = []
        for i in range(1, len(sessions) + 1):
            rs = [r for r in by_page.get(label, []) if r["sessionIndex"] == i]
            if not rs:
                cells.append("—")
            else:
                cells.append("、".join(
                    f"{r['mmss']}" + ("（重放）" if r["isReplay"] else "")
                    + (f"–{_mmss(r['endSec'])}" if r.get("endSec") else "")
                    for r in rs))
        proj_rows.append(
            f"| {label} | {sl.get('title') or '—'} | {sl.get('contentType') or 'unknown'} | "
            + " | ".join(cells) + " |")
    projection_table = "\n".join(proj_rows)

    # ── 内容类型 callout ──────────────────────────────────────────────────
    ct_blocks: List[str] = []
    for sl in slides:
        ct = sl.get("contentType")
        if ct == "video":
            ct_blocks.append(
                f"> [!important] {_page_label(sl)} 不是坏页，是一段未导出的视频\n"
                "> 该页的 PDF 区域是**视频占位未被导出**，不是排版错误。\n"
                "> 视频内容与具体讲解口径**待视频或转写确认**。")
        elif ct == "desktop":
            ct_blocks.append(
                f"> [!note] {_page_label(sl)} 是教师桌面/应用窗口截图\n"
                "> **不是课件页**，不能当作课件内容引用。")
        elif ct == "activity":
            ct_blocks.append(
                f"> [!note] {_page_label(sl)} 是课堂活动/练习页")
    content_type_callouts = "\n\n".join(ct_blocks)

    # ── 页脚对照表（必须带「所属小节/单元」与「该小节应印」两列）────────────
    # 页脚是**小节编号**：同小节多页共享同一编号是正常的，所以这一列会出现重复。
    # 只给一个"页码"列会让读者误以为编号连续且唯一。
    frows = []
    for sl in slides:
        raw = sl.get("pageNoFooterOcr") or sl.get("pageNoFullOcr")
        raw_txt = "**无页脚数字**" if raw is None else f"`{raw}`"
        st = sl.get("pageNoStatus") or "—"
        mark = "" if st == "verified" else "**"
        frows.append(
            f"| {_page_label(sl)} | {raw_txt} | {sl.get('pageNoNormalized') or '—'} | "
            f"{mark}{st}{mark} | {sl.get('footerUnit') or '—'} |")
    footer_rows = "\n".join(frows)

    # ── 逐页索引 ──────────────────────────────────────────────────────────
    entries: List[str] = []
    for sl in slides:
        label = _page_label(sl)
        title = sl.get("title") or "（无标题）"
        entries.append(f"### {label} — {title}")
        entries.append("")
        entries.append(f"> {_force_expanded((profile.get('callouts') or {}).get('slideBlock')
                                            or '[!example]')} "
                       f"PDF 阅读页 {sl.get('pdfPage') if sl.get('pdfPage') is not None else '—'}"
                       f"｜{title}")
        entries.append(f"> {emb.embed(sl['file'], width=emb.deck_width)}")
        entries.append("")
        meta = []
        if sl.get("contentType") and sl["contentType"] != "slide":
            meta.append(f"- **内容类型：** {CONTENT_TYPE_ZH.get(sl['contentType'], sl['contentType'])}")
        pr = by_page.get(label, [])
        if pr:
            meta.append("- **课堂放映：** " + "；".join(
                f"{r['sessionLabel']} {r['mmss']}"
                + ("（重放）" if r["isReplay"] else "")
                + (f"–{_mmss(r['endSec'])}" if r.get("endSec") else "")
                + (f"　时间码口径 `{r['timecodeSource']}`"
                   + (f"，平台原始读数 `{r['rawTimecode']}`" if r.get("rawTimecode") else "")
                   if r.get("timecodeSource") else "")
                for r in pr))
        else:
            meta.append("- **课堂放映：** 无——`projections` 为空，**未见对应讲解**"
                        "（课件存在 ≠ 课堂讲过）")
        if sl.get("footerUnit"):
            meta.append(f"- **所属单元：** {sl['footerUnit']}")
        if sl.get("pageNoStatus") and sl["pageNoStatus"] != "verified":
            meta.append(f"- **页脚：** {FOOTER_STATUS_ZH.get(sl['pageNoStatus'], sl['pageNoStatus'])}"
                        + (f"　原始读数「{sl.get('pageNoFooterOcr') or sl.get('pageNoFullOcr')}」"
                           if (sl.get("pageNoFooterOcr") or sl.get("pageNoFullOcr")) else ""))
            if sl.get("pageNoNote"):
                meta.append(f"  - 判断依据：{sl['pageNoNote']}")
        if sl.get("isPureImagePage"):
            meta.append("- **纯图页：** 文本提取为空，必须以页面图像为准")
        if sl.get("textExtractionIssue"):
            meta.append(f"- **文本提取问题：** {sl['textExtractionIssue']}")
        meta.append("- **本页在讲什么：** （待补）")
        entries.append("\n".join(meta))
        entries.append("")
    page_entries = "\n".join(entries)

    # ── 待核实 ────────────────────────────────────────────────────────────
    tvs = collect_to_verify(manifest)
    to_verify = to_verify_lines(tvs)
    for w in (manifest.get("warnings") or []):
        to_verify += f"\n- [ ] **manifest.warnings** — {w}"

    connections = "\n".join([
        f"- [[{profile.get('dirs', {}).get('lecture') or '02 课堂笔记'}/{lecture_id} — "
        f"（待补标题）|{lecture_id} 课堂笔记]]",
        f"- [[{profile.get('dirs', {}).get('sourceRegister') or '09 参考资料/Source Register'}"
        f"|资料登记表 Source Register]]",
    ])

    mapping = {
        "note_type_key": fm_key,
        "deck_type_value": deck_type_value,
        "course_code": course.get("code") or "（未记录课程代码）",
        "lecture_id": lecture_id,
        "deck_id": deck_id,
        "deck_file": pdf.get("file") or "（未记录）",
        "material_date": src["capturedAt"][:10],
        "status": "indexed" if all(s.get("pageNoStatus") for s in slides) else "indexed-partial",
        "source_lines": "\n".join(source_lines),
        "tag_lines": "\n".join(tags),
        "title_zh": "（待补：中文主题）",
        "title_en": "（待补 English Topic）",
        "page_count": str(page_count),
        "source_register_line": f"[[{sr}]]" if sr else "（画像未给出 Source Register 落点）",
        "page_image_line": (
            f"{len(slides)} 页**全部直接内嵌**对应的渲染页图"
            "（`slides[].file`），每一页都能在页内直接看到，"
            "**不依赖 PDF 页码跳转**——" + emb.page_link_refused()),
        "deck_scope_callouts": scope_callouts,
        "content_structure": content_structure,
        "checkpoint_note": checkpoint_note,
        "projection_table": projection_table,
        "content_type_callouts": content_type_callouts,
        "footer_rows": footer_rows,
        "page_entries": page_entries,
        "to_verify": to_verify,
        "connections": connections,
    }
    return _fill(template, mapping)


# ═══════════════════════════════════════════════════════════════════════════
# V 回放复核页
# ═══════════════════════════════════════════════════════════════════════════

def build_timeline(manifest: Dict[str, Any], profile: Dict[str, Any],
                   template: str) -> str:
    src = manifest["source"]
    course = src["course"]
    sessions = src["sessions"]
    slides = sorted(manifest["slides"], key=_norm_page_sort)
    req = profile.get("frontmatter", {}).get("required") or []
    fm_key = "note_type" if "note_type" in req else "type"
    lecture_id = next((s.get("lectureId") for s in sessions if s.get("lectureId")), None) or "L??"
    tl_id = lecture_id.replace("L", "V")

    sess_rows = []
    for i, s in enumerate(sessions, 1):
        t = s.get("transcript") or {}
        sess_rows.append(
            f"| {i} | {s.get('label', '')} | {s.get('platformSessionId') or '—'} | "
            f"{s.get('classDate') or '—'} | {_mmss(s.get('durationSec')) if s.get('durationSec') else '—'} | "
            f"`{s.get('usedDeck') or '—'}` | {s.get('deckCoverageNote') or '—'} |")
    session_rows = "\n".join(sess_rows) or "| — | （无节次） | | | | | |"

    rows = project_rows(manifest)
    tl_rows = []
    for r in rows:
        tl_rows.append(
            f"| {r['mmss']}"
            + (f"–{_mmss(r['endSec'])}" if r.get("endSec") else "")
            + f" | {r['sessionLabel']} | {r['page']} | {r['title']} | "
            f"{r['contentType']} | {'是' if r['isReplay'] else '—'} | "
            f"`{r['timecodeSource'] or '—'}` | `{r['rawTimecode'] or '—'}` |")
    timeline_rows = "\n".join(tl_rows) or "| — | | | | | | | |"

    nt = [s for s in slides if not is_taught(s)]
    if nt:
        no_projection_note = (
            "下列页 `projections` 为空，**未见对应讲解**：\n\n"
            + "、".join(f"**{_page_label(s)}**（{s.get('title') or '—'}）" for s in nt)
            + "\n\n> 课件存在 ≠ 课堂讲过。这些页只能写「课件版 / 未见对应讲解」。")
    else:
        no_projection_note = "无——本课件每一页都有放映记录。"

    media_rows = []
    for i, s in enumerate(sessions, 1):
        md = s.get("media") or {}
        t = s.get("transcript") or {}
        media_rows.append(
            f"| {i} | `{md.get('videoPath') or '—'}` | `{md.get('audioPath') or '—'}` | "
            f"{'是' if md.get('archived') else '否（临时输入，处理后清理）'} | "
            f"`{t.get('file') or '—'}` | {t.get('model') or '—'} | "
            f"{t.get('chunkSec') or '—'} | {t.get('segmentCount') if t.get('segmentCount') is not None else '—'} | "
            f"{'是（推断）' if t.get('diarized') else '否'} | {md.get('note') or t.get('qualityNote') or '—'} |")
    media_table = "\n".join(media_rows) or "| — | | | | | | | | | |"

    mp = profile.get("mediaPolicy") or {}
    media_policy_line = (
        f"本库媒体政策：视频归档 `{mp.get('archiveVideo')}`、音频归档 `{mp.get('archiveAudio')}`、"
        f"转写归档 `{mp.get('archiveTranscript')}`。"
        + ("原始媒体已清理时，Source Register 只登记来源与「原始媒体未归档」状态，"
           "**不建失效链接**。" if mp.get("registerDeletedMedia") else ""))

    unarch = manifest.get("unarchivedMaterials") or []
    unarchived_rows = "\n".join(
        f"| {m['what']} | {m['evidence']} | {m.get('impact') or '—'} |" for m in unarch
    ) or "| （无） | | |"
    if unarch:
        unarchived_rows = ("| 材料 | 依据 | 对结论的限制 |\n|---|---|---|\n" + unarchived_rows)

    tvs = collect_to_verify(manifest)
    mapping = {
        "note_type_key": fm_key,
        "course_code": course.get("code") or "（未记录课程代码）",
        "lecture_id": lecture_id,
        "timeline_id": tl_id,
        "class_date": "；".join(sorted({s["classDate"] for s in sessions if s.get("classDate")})) or "未确认",
        "status": "replay-review",
        "source_lines": "\n".join(f'  - "{s.get("label", "")}"' for s in sessions),
        "tag_lines": "\n".join([f"  - {course.get('code', '').lower() or 'course'}",
                                f"  - {tl_id.lower()}", "  - replay"]),
        "title_zh": "（待补：中文主题）",
        "title_en": "（待补 English Topic）",
        "lecture_link": f"{profile.get('dirs', {}).get('lecture') or '02 课堂笔记'}/{lecture_id}",
        "session_rows": session_rows,
        "timeline_rows": timeline_rows,
        "no_projection_note": no_projection_note,
        "media_rows": media_table,
        "media_policy_line": media_policy_line,
        "unarchived_rows": unarchived_rows,
        "to_verify": to_verify_lines(tvs),
        "connections": f"- [[{profile.get('dirs', {}).get('lecture') or '02 课堂笔记'}/{lecture_id} — （待补）|{lecture_id} 课堂笔记]]",
    }
    return _fill(template, mapping)


# ═══════════════════════════════════════════════════════════════════════════
# 硬规则强制 + 产物自检
# ═══════════════════════════════════════════════════════════════════════════

def _force_expanded(callout: str) -> str:
    """把 `[!example]-` 强制改成展开形式。

    这是**机械兜底**：写作或模板里漏了减号，图片就会默认不可见。
    有机化学库实测 72/72 篇 L 笔记打开时可见图数为 0，根因就是这个减号。
    """
    return re.sub(r"^(\[!\w+])-$", r"\1+", callout.strip())


RE_FOLDED_IMAGE_CALLOUT = re.compile(
    r"^>\s*\[!\w+\]-\s*.*$(?:\n>\s*!\[\[.*$)+", re.M)


def audit_output(text: str, name: str) -> List[str]:
    """写盘前自检产物。任一红即不该交付。"""
    problems: List[str] = []

    # 硬规则 1：不得出现折叠的图片块
    folded = [m.group(0).splitlines()[0] for m in RE_FOLDED_IMAGE_CALLOUT.finditer(text)]
    if folded:
        problems.append(f"{name}: 出现 {len(folded)} 处折叠图片块（图片默认不可见）："
                        + "；".join(folded[:3]))

    # 硬规则 2：不得用 `#page=` 做图片嵌入
    for m in re.finditer(r"!\[\[[^\]]*#page=[^\]]*\]\]", text):
        problems.append(f"{name}: 图片嵌入里出现 `#page=`（心理库实测点击无效）：{m.group(0)}")
    for m in re.finditer(r"!\[[^\]]*\]\([^)]*#page=", text):
        problems.append(f"{name}: 标准 Markdown 图片里出现 `#page=`：{m.group(0)}")

    # 硬规则 3：表格内嵌图片必须转义竖线
    for m in re.finditer(r"^\|.*!\[\[[^\]]*(?<!\\)\|[^\]]*\]\].*$", text, re.M):
        line = m.group(0)
        if not re.search(r"!\[\[[^\]]*\\\|", line):
            problems.append(f"{name}: 表格内嵌图片未转义竖线：{line[:90]}")

    # 不写本机绝对路径
    for m in re.finditer(r"(?:C:\\|[A-Za-z]:\\\\)[^\s\])|]+", text):
        problems.append(f"{name}: 出现疑似本机绝对路径：{m.group(0)[:70]}")

    # 不留未替换的占位符
    left = sorted(set(re.findall(r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}", text)))
    if left:
        problems.append(f"{name}: 有未替换的占位符 {left}")

    return problems


# ═══════════════════════════════════════════════════════════════════════════
# Git 门
# ═══════════════════════════════════════════════════════════════════════════

def _git(vault: str, *args: str, timeout: int = 60) -> Tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", vault, *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "git 不可用"
    except subprocess.TimeoutExpired:
        return 124, "git 超时"


def git_gate(vault: str, profile: Dict[str, Any], dry_run: bool,
             allow_pull: bool = False) -> Tuple[bool, List[str]]:
    """写入前跑 Git 门：status → fetch → 比对远端 → 干净才 pull。

    只读操作（status / fetch / rev-list / branch）永远允许；
    **pull 只在工作区干净且画像要求时才做**；其余任何写操作本脚本都不做。

    返回 (通过?, 消息列表)。
    """
    msgs: List[str] = []
    gitc = profile.get("git") or {}

    if not os.path.isdir(os.path.join(vault, ".git")):
        return True, ["Git 门：目标库不是 Git 仓库，跳过（本 skill 只写笔记文件）。"]

    rc, out = _git(vault, "rev-parse", "--is-inside-work-tree")
    if rc != 0:
        return True, ["Git 门：不在 Git 工作树内，跳过。"]

    dirty = False
    if gitc.get("requireStatusBeforeEdit", True):
        rc, out = _git(vault, "status", "--short", "--branch")
        msgs.append("git status --short --branch:\n" + out.strip())
        if rc != 0:
            return False, msgs + ["Git 门失败：git status 返回非 0。"]
        # 只看已跟踪文件的改动；未跟踪文件不阻断（本 skill 只新增笔记）
        tracked_changes = [l for l in out.splitlines()[1:]
                           if l.strip() and not l.startswith("??")]
        dirty = bool(tracked_changes)
        if dirty:
            msgs.append("⚠ 工作区有已跟踪文件的未提交修改：\n  " + "\n  ".join(tracked_changes))
            msgs.append("  → 按目标库 AGENTS.md：**不 pull、不合并**；"
                        "只能新增文件，不要覆盖既有笔记。")

    if gitc.get("requireFetchBeforeEdit", True):
        rc, out = _git(vault, "fetch", "origin")
        if rc != 0:
            msgs.append("⚠ git fetch origin 失败（可能是离线或没有 origin）："
                        + out.strip()[:200])
            msgs.append("  → 目标库 AGENTS.md 要求「无法连接时立即停止本库的笔记编辑并报告错误」。"
                        "**本次不写盘**。")
            return False, msgs
        msgs.append("git fetch origin：OK（只更新远端跟踪信息，不改文件）")

        rc, out = _git(vault, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if rc == 0 and out.strip():
            upstream = out.strip()
            rc, out = _git(vault, "rev-list", "--left-right", "--count",
                           f"HEAD...{upstream}")
            if rc == 0 and out.strip():
                behind = out.split()[1] if len(out.split()) >= 2 else "?"
                msgs.append(f"与 {upstream} 的差异（本地…远端）：{out.strip()}")
                if behind != "0":
                    msgs.append(f"⚠ 远端有本地尚未包含的 {behind} 个提交。"
                                "按 AGENTS.md：先读差异再决定如何安全继续。**本次不写盘**。")
                    return False, msgs
        else:
            msgs.append("⚠ 当前分支没有上游跟踪分支，无法比对远端。")

    if gitc.get("pullOnlyWhenClean", True):
        if dirty:
            msgs.append("pullOnlyWhenClean：工作区不干净 → **不 pull**。")
        elif allow_pull:
            rc, out = _git(vault, "pull", "--ff-only", timeout=120)
            msgs.append(f"git pull --ff-only：rc={rc} {out.strip()[:200]}")
            if rc != 0:
                return False, msgs + ["Git 门失败：pull --ff-only 未成功，保留完整提示。"]
        else:
            msgs.append("工作区干净，但未传 `--allow-pull` → **不 pull**（本 skill 默认不主动同步）。")

    msgs.append("Git 门：通过（本脚本只写笔记文件，不做 add/commit/push；"
                "暂存由人工按 stageOnlyTargetFiles 执行）。")
    return True, msgs


# ═══════════════════════════════════════════════════════════════════════════
# 落盘
# ═══════════════════════════════════════════════════════════════════════════

def safe_name(s: str) -> str:
    """文件名里不使用方括号（会破坏 Obsidian 的 wikilink 与嵌入语法）。"""
    out = s.replace("[", "(").replace("]", ")")
    return re.sub(r'[<>:"/\\|?*]', "-", out).strip()


def write_artifact(vault: str, rel_dir: str, filename: str, text: str,
                   apply: bool, force: bool, out_dir: Optional[str]) -> Dict[str, Any]:
    """写一个产物。

    规则：默认**不覆盖**已有笔记，改写 `<name>.new.md`；`--force` 才覆盖。
    dry-run 时写 `out_dir`（临时目录）或只报路径。
    """
    target_dir = os.path.join(vault, rel_dir) if rel_dir else vault
    target = os.path.join(target_dir, filename)
    existed = os.path.isfile(target)
    if existed and not force:
        base, ext = os.path.splitext(filename)
        filename = f"{base}.new{ext}"
        target = os.path.join(target_dir, filename)

    rec = {"path": target, "existed": existed, "overwritten": existed and force,
           "written": False, "reason": ""}
    if not apply:
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            tmp = os.path.join(out_dir, filename)
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
            rec.update(written=True, path=tmp,
                       reason="dry-run：写入临时目录，未碰知识库")
        else:
            rec["reason"] = "dry-run：未写盘"
        return rec
    os.makedirs(target_dir, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(text)
    rec.update(written=True, reason="已写入")
    return rec


# ═══════════════════════════════════════════════════════════════════════════
# 样例 manifest（L08 真实素材，用于 --sample 自测）
# ═══════════════════════════════════════════════════════════════════════════

# (pdfPage, title, footerUnit, pageNoFooterOcr, pageNoStatus, contentType, isPureImage)
L08_PAGES: List[Tuple[int, str, str, Optional[str], str, str, bool]] = [
    (1,  "Memory-guided saccades", "EYE TRACKING", "01", "verified", "slide", False),
    (2,  "Why does the task include a delay?", "EYE TRACKING", "02", "verified", "slide", False),
    (3,  "Spatial accuracy", "EYE TRACKING", "03", "verified", "slide", False),
    (4,  "Timing: when did the eyes move?", "EYE TRACKING", None, "missing", "slide", False),
    (5,  "Common metrics for saccade", "EYE TRACKING", None, "missing", "slide", False),
    (6,  "Applications", "EYE TRACKING", "04", "verified", "slide", False),
    (7,  "Memory systems and knowledge representation（单元导入）",
         "KNOWLEDGE REPRESENTATION", "02", "verified", "slide", False),
    (8,  "Taxonomy of long-term memory", "KNOWLEDGE REPRESENTATION", "03", "verified", "slide", True),
    (9,  "Three forms of long-term memory", "KNOWLEDGE REPRESENTATION", "03", "verified", "slide", True),
    (10, "Procedural memory and implicit learning", "KNOWLEDGE REPRESENTATION",
         "实验心理学 11", "verified", "slide", True),
    (11, "Declarative vs. procedural memory", "KNOWLEDGE REPRESENTATION", "03", "verified", "slide", True),
    (12, "What H.M. taught us about memory", "KNOWLEDGE REPRESENTATION",
         "实验心理学 06", "verified", "slide", True),
    (13, "Formation of memory（视频）", "KNOWLEDGE REPRESENTATION", None, "missing", "video", False),
    (14, "The evidence for sleep to consolidate memory", "KNOWLEDGE REPRESENTATION", None, "missing", "slide", False),
    (15, "Timing for sleep / Sleep stages", "KNOWLEDGE REPRESENTATION", None, "missing", "slide", False),
    (16, "Semantic and episodic memory tests", "KNOWLEDGE REPRESENTATION", "05", "verified", "slide", False),
    (17, "Tulving (1972) 定义", "KNOWLEDGE REPRESENTATION", "06", "verified", "slide", False),
    (18, "Capacity and duration", "KNOWLEDGE REPRESENTATION", "06", "verified", "slide", False),
    (19, "Visual memory retains more than object categories", "KNOWLEDGE REPRESENTATION", "07", "verified", "slide", False),
    (20, "No known duration limits", "KNOWLEDGE REPRESENTATION", "07", "verified", "slide", False),
    (21, "How our knowledge is represented", "KNOWLEDGE REPRESENTATION", "07", "verified", "slide", False),
    (22, "单元导入（含 Encoding, storage, retrieval）", "KNOWLEDGE REPRESENTATION", "02", "verified", "slide", False),
    (23, "Knowledge organization and retrieval", "KNOWLEDGE REPRESENTATION", "08", "verified", "slide", False),
    (24, "Semantic and episodic memory in the brain", "KNOWLEDGE REPRESENTATION", "42", "anomalous", "slide", False),
    (25, "Category verification", "KNOWLEDGE REPRESENTATION", "09", "verified", "slide", False),
    (26, "Typicality", "KNOWLEDGE REPRESENTATION", "10", "verified", "slide", False),
    (27, "Typicality effect", "KNOWLEDGE REPRESENTATION", "10", "verified", "slide", False),
    (28, "金丝雀示例 1", "KNOWLEDGE REPRESENTATION", "10", "verified", "slide", False),
    (29, "Semantic memory - Example 2（范畴大小效应）", "KNOWLEDGE REPRESENTATION", "10", "verified", "slide", False),
    (30, "狗示例 3", "KNOWLEDGE REPRESENTATION", "10", "verified", "slide", False),
    (31, "Familiarity effect", "KNOWLEDGE REPRESENTATION", "10", "verified", "slide", False),
    (32, "Category level and familiarity", "KNOWLEDGE REPRESENTATION", "11", "verified", "slide", False),
    (33, "Negative judgement effect", "KNOWLEDGE REPRESENTATION", "10", "verified", "slide", True),
    (34, "Rejecting a false statement", "KNOWLEDGE REPRESENTATION", "12", "verified", "slide", False),
    (35, "Four patterns", "KNOWLEDGE REPRESENTATION", "13", "verified", "slide", False),
]

# 放映记录：pdfPage -> [(sessionIndex, mmss, sec, endSec, isReplay)]
L08_PROJECTIONS: Dict[int, List[Tuple[int, str, float, Optional[float], bool]]] = {
    1:  [(1, "35:07", 2107, None, False)],
    2:  [(1, "37:32", 2252, None, False)],
    3:  [(1, "39:18", 2358, None, False)],
    4:  [(1, "40:54", 2454, None, False)],
    5:  [(1, "42:15", 2535, None, False)],
    6:  [(1, "43:35", 2615, None, False)],
    7:  [(1, "45:46", 2746, None, False)],
    8:  [(1, "47:28", 2848, None, False)],
    9:  [(1, "49:13", 2953, None, False), (2, "00:01", 1, None, False)],
    10: [(2, "06:03", 363, None, False)],
    11: [(2, "08:39", 519, None, False)],
    12: [(2, "10:16", 616, None, False)],
    13: [(2, "12:51", 771, 947, False)],
    14: [(2, "17:00", 1020, None, False)],
    15: [(2, "18:49", 1129, None, False)],
    16: [(2, "22:22", 1342, None, False)],
    17: [(2, "23:58", 1438, None, False)],
    18: [(2, "26:41", 1601, None, False)],
    19: [(2, "29:25", 1765, None, False)],
    20: [(2, "31:35", 1895, None, False)],
    21: [(2, "33:25", 2005, None, False)],
    22: [(2, "35:12", 2112, None, False)],
    23: [(2, "35:34", 2134, None, False)],
    24: [(2, "37:28", 2248, None, False)],
    25: [(2, "38:44", 2324, None, False)],
    26: [(2, "39:17", 2357, None, False)],
    27: [(2, "40:23", 2423, None, False)],
    28: [(2, "40:48", 2448, None, False)],
    29: [(2, "41:12", 2472, None, False), (2, "42:49", 2569, None, True)],
    30: [(2, "41:59", 2519, None, False)],
    31: [(2, "42:19", 2539, None, False), (2, "42:52", 2572, None, True)],
    32: [(2, "43:15", 2595, None, False)],
    33: [(2, "44:03", 2643, None, False)],
    34: [(2, "44:51", 2691, None, False)],
    35: [(2, "45:42", 2742, None, False)],
}


def sample_manifest(att_root: str = "09 参考资料/附件/L08 课件 PDF") -> Dict[str, Any]:
    """L08 真实素材样例。**只用本 skill 已核实的事实**，不补编。"""
    slides = []
    for i, (pg, title, unit, footer, status, ctype, pure) in enumerate(L08_PAGES, 1):
        proj = []
        for (si, mmss, sec, end, replay) in L08_PROJECTIONS.get(pg, []):
            entry: Dict[str, Any] = {
                "sessionIndex": si, "startSec": sec, "startMmss": mmss,
                "timecodeSource": "platform-imageSeekTime-sec",
                "rawTimecode": str(int(sec)),
            }
            if end is not None:
                entry["endSec"] = end
            if replay:
                entry["isReplay"] = True
            proj.append(entry)
        sl: Dict[str, Any] = {
            "index": i,
            "file": f"{att_root}/PDF p{pg:02d}.jpg",
            "source": "pdf-render",
            "pdfPage": pg,
            "projections": proj,
            "title": title,
            "contentType": ctype,
            "footerUnit": unit,
            "pageNoScope": "section",
        }
        if footer is not None:
            sl["pageNoFooterOcr"] = footer
            sl["pageNoFullOcr"] = footer
            sl["pageNoNormalized"] = re.sub(r"\D", "", footer) or None
        else:
            sl["pageNoFullOcr"] = None
            sl["pageNoFooterOcr"] = None
            sl["pageNoNormalized"] = None
        sl["pageNoStatus"] = status
        if status == "anomalous":
            sl["pageNoNote"] = ("页脚读数为 42，而同一单元前后页（p23=08、p25=09）"
                                "分别是 08 与 09；两路 OCR 读数一致，"
                                "按目标库 AGENTS.md「保留原读数、不静默改名」处理，"
                                "引用以 PDF 阅读页 p24 为准。")
        elif status == "missing":
            sl["pageNoNote"] = ("该页无页脚数字"
                                + ("（p5 底部为「数据来源」内容行）" if pg == 5 else "")
                                + "；不推断其编号。")
        if pure:
            sl["isPureImagePage"] = True
        if pg == 14:
            sl["textExtractionIssue"] = ("纯文本提取顺序错乱：两方框标题与正文交错，"
                                         "实际配对为 Declarative↔单词对、Procedural↔运动技能")
        # pageNoExpected：单元内应印编号。**不等于 pdfPage**，这里只对已知的填。
        expected = {
            1: "01", 2: "02", 3: "03", 6: "04", 7: "02", 8: "03", 9: "03",
            11: "03", 16: "05", 17: "06", 18: "06", 19: "07", 20: "07", 21: "07",
            22: "02", 23: "08", 24: "08", 25: "09", 26: "10", 27: "10", 28: "10",
            29: "10", 30: "10", 31: "10", 32: "11", 33: "10", 34: "12", 35: "13",
        }
        sl["pageNoExpected"] = expected.get(pg)
        slides.append(sl)

    return {
        "schemaVersion": 1,
        "source": {
            "platform": "canvas",
            "course": {"id": "unknown", "name": "心理与精神健康", "code": "HLT3202G"},
            "capturedAt": "2026-10-15T10:00:00+08:00",
            "platformSubtitles": "none",
            "sessions": [
                {
                    "label": "L08 第15节 1355",
                    "lectureId": "L08",
                    "platformSessionId": "179741",
                    "letiNumber": 15,
                    "classDate": "2026-10-13",
                    "partOfSplit": 1,
                    "durationSec": 3308.6,
                    "usedDeck": "第9次课.pptx",
                    "deckCoverageNote": "0:00–35:07 未使用 S84（第8次课.pdf）",
                    "media": {
                        "videoPath": "09 参考资料/附件/L08 课堂视频/L08 第15节 stream2 (record5).mp4",
                        "archived": False,
                        "note": "原始 MP4 未随仓库提交，需从平台重新获取",
                    },
                    "transcript": {
                        "file": "09 参考资料/附件/L08 字幕/L08 第15节 1355.md",
                        "model": "large-v3", "chunkSec": 600, "segmentCount": 412,
                        "diarized": True, "speakerLabelsAreInferred": True,
                        "knownMishearings": ["declarative→decorative"],
                    },
                    "platformArtifacts": [{
                        "kind": "ppt-track-manifest",
                        "file": "raw/L08 第15节 ai-ppt.json",
                        "note": "imageSeekTime 单位为秒（实测 max 2953 对应时长 3308.6）",
                        "recordCount": 68,
                    }],
                },
                {
                    "label": "L08 第16节 1450",
                    "lectureId": "L08",
                    "platformSessionId": "180045",
                    "letiNumber": 16,
                    "classDate": "2026-10-13",
                    "partOfSplit": 2,
                    "durationSec": 3289.0,
                    "media": {
                        "videoPath": "09 参考资料/附件/L08 课堂视频/L08 第16节 stream2 (record5).mp4",
                        "archived": False,
                        "note": "原始 MP4 未随仓库提交，需从平台重新获取",
                    },
                    "transcript": {
                        "file": "09 参考资料/附件/L08 字幕/L08 第16节 1450.md",
                        "model": "large-v3", "chunkSec": 600, "segmentCount": 388,
                        "diarized": True, "speakerLabelsAreInferred": True,
                    },
                    "platformArtifacts": [{
                        "kind": "ppt-track-manifest",
                        "file": "raw/L08 第16节 ai-ppt.json",
                        "note": "imageSeekTime 单位为秒（实测 max 3210 对应时长 3289.0）",
                        "recordCount": 63,
                    }],
                },
            ],
        },
        "slides": slides,
        "pdf": {
            "file": "09 参考资料/附件/第8次课.pdf",
            "pageCount": 35,
            "author": "教师端导出",
            "createdAt": "2026-10-12T21:29:00+08:00",
            "isSelection": True,
            "textExtractionIssues": [
                "p8、p11、p13、p20、p21、p28–p31、p33 为纯图页（文本提取为空）",
                "p14 纯文本提取顺序错乱",
            ],
            "note": "p13 嵌入视频未导出",
        },
        "sourceRegisterEntries": [
            {"id": None, "type": "课程回放页", "source": "SJTU 回放 第15节 13:55–14:50",
             "date": "2026-10-15", "status": "已按平台课件轨核对", "note": "L08 第15节"},
            {"id": None, "type": "课程回放页", "source": "SJTU 回放 第16节 14:50–15:45",
             "date": "2026-10-15", "status": "已按平台课件轨核对", "note": "L08 第16节"},
            {"id": None, "type": "课堂转写", "source": "L08 第15节 1355.md / 第16节 1450.md",
             "date": "2026-10-15", "status": "已转录", "note": "faster-whisper large-v3，600 秒分段"},
        ],
        "unarchivedMaterials": [
            {"what": "教师端 `第9次课.pptx`",
             "evidence": "L08 第15节 0:00–35:07 使用；文件名由回放画面读到",
             "impact": "该段内容的页面无法在 S84 索引中定位；**S84 不是本次课件的全部**"},
            {"what": "S84 之外的 2 页（p35 之后的讨论页）",
             "evidence": "第16节 46:41、48:28 放映；不在 S84 的 35 页内",
             "impact": "S84 是选页；这两页未编号，需取得完整课件后补录"},
        ],
        "warnings": [
            "p24 页脚读数 42 与所属单元序列不符（前后页 08/09）；已按 anomalous 保留原读数。",
            "第15节 0:00–35:07 不在 S84 范围内（教师端 pptx 未归档）。",
            "S84 为选页：p35 之后课堂另放映 2 页，未包含在 S84 内。",
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════

def minimal_validate_manifest(man: Dict[str, Any], schema_path: str) -> List[str]:
    """优先用 jsonschema；否则用 build_vault_profile 里的最小校验器。"""
    if not os.path.isfile(schema_path):
        return [f"找不到 schema：{schema_path}"]
    schema = _read_json(schema_path)
    try:
        import jsonschema  # type: ignore
        jsonschema.validate(man, schema)
        return []
    except ImportError:
        sys.path.insert(0, HERE)
        import build_vault_profile as B  # type: ignore
        return B.minimal_validate(man, schema)
    except Exception as exc:
        return [str(exc)]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="从 manifest + 知识库画像生成 L/P/V/Concept 骨架、索引更新与 Source Register 行")
    ap.add_argument("--manifest", help="manifest.json 路径")
    ap.add_argument("--vault", required=True, help="知识库根目录")
    ap.add_argument("--profile", default=None,
                    help="画像 json；默认 <vault>/_scripts/vault-profile.json")
    ap.add_argument("--dry-run", action="store_true", default=None,
                    help="只打印/写临时目录，不碰知识库（**默认行为**）")
    ap.add_argument("--apply", action="store_true", help="真正写盘（会跑 Git 门）")
    ap.add_argument("--force", action="store_true",
                    help="覆盖已有笔记（默认改写 <name>.new.md）")
    ap.add_argument("--with-timeline", action="store_true",
                    help="额外生成 V## 回放复核页（默认不建——三库都要求按需）")
    ap.add_argument("--with-concept", action="append", default=[],
                    metavar="TERM", help="生成 Concept 骨架（可重复）")
    ap.add_argument("--out-dir", default=None,
                    help="dry-run 时把产物写到这个临时目录，便于人工比对")
    ap.add_argument("--sample", action="store_true",
                    help="用内置的 L08 真实素材样例 manifest（不需要 --manifest）")
    ap.add_argument("--validate-manifest", action="store_true",
                    help="先按 schemas/manifest.schema.json 校验 manifest")
    ap.add_argument("--allow-pull", action="store_true",
                    help="工作区干净时允许 git pull --ff-only")
    args = ap.parse_args(argv)

    # ── manifest ──────────────────────────────────────────────────────────
    if args.sample:
        manifest = sample_manifest()
        src_note = "内置样例（L08 真实素材）"
    elif args.manifest:
        if not os.path.isfile(args.manifest):
            print(f"[gen_notes] manifest 不存在：{args.manifest}", file=sys.stderr)
            return 2
        manifest = _read_json(args.manifest)
        src_note = args.manifest
    else:
        print("[gen_notes] 需要 --manifest <json> 或 --sample", file=sys.stderr)
        return 2

    if args.validate_manifest or args.sample:
        schema_path = os.path.join(HERE, "..", "schemas", "manifest.schema.json")
        errs = minimal_validate_manifest(manifest, schema_path)
        if errs:
            print("[gen_notes] manifest 校验失败：", file=sys.stderr)
            for e in errs[:20]:
                print("  -", e, file=sys.stderr)
            return 3
        print("[gen_notes] manifest 校验：OK")

    # ── 画像 ──────────────────────────────────────────────────────────────
    profile_path = args.profile or os.path.join(args.vault, "_scripts", "vault-profile.json")
    if not os.path.isfile(profile_path):
        print(f"[gen_notes] 找不到画像：{profile_path}\n"
              "  先跑 build_vault_profile.py（画像没生成成功就不要往下走）。", file=sys.stderr)
        return 2
    profile = _read_json(profile_path)
    if profile.get("schemaVersion") != 1:
        print(f"[gen_notes] 画像 schemaVersion={profile.get('schemaVersion')}，本脚本只认 1。",
              file=sys.stderr)
        return 2

    apply = bool(args.apply)
    dry_run = not apply
    if apply and not profile.get("vault", {}).get("reviewedByHuman"):
        print("[gen_notes] 画像 `reviewedByHuman = false`：**人工复核前只允许只读操作**。\n"
              "  请人工复核画像后再用 --apply；本次以 dry-run 运行。", file=sys.stderr)
        apply = False
        dry_run = True

    print(f"[gen_notes] manifest = {src_note}")
    print(f"[gen_notes] vault    = {args.vault}")
    print(f"[gen_notes] profile  = {profile_path}"
          f"（reviewedByHuman={profile.get('vault', {}).get('reviewedByHuman')}）")
    print(f"[gen_notes] 模式     = {'APPLY（写盘）' if apply else 'DRY-RUN（不碰知识库）'}")
    if profile.get("_warnings"):
        print(f"[gen_notes] 画像有 {len(profile['_warnings'])} 条 warnings（写盘前请人工看一遍）")

    # 推断出来的要求不是要求：必须显式提示，否则下游会拿它当硬要求报红灯。
    fm_block = profile.get("frontmatter") or {}
    if fm_block.get("requiredIsInferred") and not profile.get("vault", {}).get("reviewedByHuman"):
        req = fm_block.get("required") or []
        rpt = fm_block.get("requiredPerNoteType") or {}
        print("[gen_notes] ⚠ frontmatter.required 是**推断值**"
              f"（requiredIsInferred=true，{len(req)} 个字段 {req}），"
              "且画像 reviewedByHuman=false。")
        print("            **复核前不要当硬要求**——校验器应降为 WARN/INFO，不得据此报 ERROR。")
        print(f"            按类型的真实必填请读 frontmatter.requiredPerNoteType"
              f"（{len(rpt)} 类）。")

    # ── 生成 ──────────────────────────────────────────────────────────────
    dirs = profile.get("dirs") or {}
    lecture_dir = dirs.get("lecture") or "02 课堂笔记"
    deck_dir = dirs.get("deck")
    timeline_dir = dirs.get("timeline") or lecture_dir
    concept_dir = dirs.get("concept")

    sessions = manifest["source"]["sessions"]
    lecture_id = next((s.get("lectureId") for s in sessions if s.get("lectureId")), None) or "L??"
    title_zh = manifest.get("titleZh") or "（待补：中文主题）"

    artifacts: List[Tuple[str, str, str]] = []   # (rel_dir, filename, text)
    audit_all: List[str] = []

    l_text = build_lecture(manifest, profile, _template("L-lecture.md"))
    l_name = safe_name(f"{lecture_id} — {title_zh}.md")
    artifacts.append((lecture_dir, l_name, l_text))
    audit_all += audit_output(l_text, l_name)

    p_text = build_deck(manifest, profile, _template("P-deck.md"))
    p_id = lecture_id.replace("L", "P")
    p_name = safe_name(f"{p_id} — 课件总览 {title_zh}.md")
    if deck_dir:
        artifacts.append((deck_dir, p_name, p_text))
        audit_all += audit_output(p_text, p_name)

    if args.with_timeline:
        v_text = build_timeline(manifest, profile, _template("V-timeline.md"))
        v_id = lecture_id.replace("L", "V")
        v_name = safe_name(f"{v_id} — 回放复核 {title_zh}.md")
        artifacts.append((timeline_dir, v_name, v_text))
        audit_all += audit_output(v_text, v_name)
    else:
        print("[gen_notes] 未生成 V##（画像/AGENTS.md：仅按需建立；要生成请加 --with-timeline）")

    for term in (args.with_concept or []):
        c_text = _fill(_template("Concept.md"), {
            "note_type_key": ("note_type"
                              if "note_type" in (profile.get("frontmatter", {}).get("required") or [])
                              else "type"),
            "course_code": manifest["source"]["course"].get("code") or "（未记录课程代码）",
            "term": term, "term_zh": "（待补）",
            "status": "seed",
            "source_lines": "  - （待补）",
            "tag_lines": "  - concept",
            "definition_zh": "（待补）", "definition_en": "（待补）",
            "why_it_matters": "- ", "mechanism": "1. ",
            "construct": "", "indicator": "", "method": "", "validity_concern": "",
            "example": "", "boundary": "- ", "evidence_notes": "- ",
            "connection_map": "- [[03 核心概念/Concept Index|核心概念索引]]",
            "active_recall": "- 它是什么？\n- 它如何被测量？\n- 什么算支持或反驳证据？",
            "sources": "- ", "to_verify": "- [ ] ",
        })
        c_name = safe_name(f"Concept — {term}.md")
        if concept_dir:
            artifacts.append((concept_dir, c_name, c_text))
            audit_all += audit_output(c_text, c_name)

    # ── 硬规则自检 ────────────────────────────────────────────────────────
    if audit_all:
        print("[gen_notes] 产物自检：**未通过**", file=sys.stderr)
        for p in audit_all:
            print("  ✗", p, file=sys.stderr)
        return 3
    print(f"[gen_notes] 产物自检：通过（{len(artifacts)} 个产物；"
          "已检查折叠图片块 / #page= / 表格转义竖线 / 本机绝对路径 / 未替换占位符）")

    # ── Source Register 行 ────────────────────────────────────────────────
    sre = manifest.get("sourceRegisterEntries") or []
    if sre:
        print("\n── Source Register 待插入行（本 skill **不擅自编号**）──")
        for e in sre:
            print(f"| {e.get('id') or '（待分配 S##）'} | {e.get('type')} | {e.get('source')} | "
                  f"{e.get('date') or '—'} | {e.get('status') or '—'} | {e.get('note') or '—'} |")
        print("  ↑ 编号由目标库分配；已删除的媒体只登记状态，**不建失效链接**。")
    if manifest.get("unarchivedMaterials"):
        print("\n── 未归档材料（必须进待核实，否则复核者以为课件完整）──")
        for m in manifest["unarchivedMaterials"]:
            print(f"  - {m['what']}｜依据：{m['evidence']}"
                  + (f"｜限制：{m['impact']}" if m.get("impact") else ""))

    # ── Git 门 + 落盘 ─────────────────────────────────────────────────────
    if apply:
        ok, msgs = git_gate(args.vault, profile, dry_run=False, allow_pull=args.allow_pull)
        for m in msgs:
            print("[git] " + m)
        if not ok:
            print("[gen_notes] Git 门未通过 → 不写盘。", file=sys.stderr)
            return 4

    print("\n── 产物 ──")
    for rel_dir, filename, text in artifacts:
        rec = write_artifact(args.vault, rel_dir, filename, text,
                             apply=apply, force=args.force, out_dir=args.out_dir)
        flag = "写入" if rec["written"] else "未写"
        note = ""
        if rec["existed"] and not args.force:
            note = "（已存在同名笔记 → 改写为 .new.md，不覆盖）"
        elif rec["overwritten"]:
            note = "（**已覆盖**）"
        print(f"  [{flag}] {rec['path']} {note}")
        print(f"        {rec['reason']}｜{len(text.splitlines())} 行 / {len(text)} 字符")

    if not apply:
        print("\n[gen_notes] DRY-RUN 结束：知识库未被修改。"
              "确认产物形状后用 --apply（会先跑 Git 门）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
