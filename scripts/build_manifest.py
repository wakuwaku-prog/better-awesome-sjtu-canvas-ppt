#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L1 素材层：把 ``raw/`` 汇总成符合 ``schemas/manifest.schema.json`` 的 ``manifest.json``。

这是采集层的**唯一出口**：下游所有层只读 manifest，不解析平台原始响应。

用法
----
::

    python build_manifest.py "<work dir>"                       # 产出 <work>/manifest.json
    python build_manifest.py "<work>" --projection-map proj.json --page-no-map pageno.json
    python build_manifest.py "<work>" --file-prefix "09 参考资料/附件/L08 课件 PDF"
    python build_manifest.py "<work>" --strict-warnings --print-summary

``raw/`` 输入契约（由 adapters 产出，也可人工补）
------------------------------------------------

::

    raw/
      source.json                    平台 / 课程 / capturedAt / platformSubtitles
      sessions/<slug>/session.json   每个平台节次一个目录，**不合并**（label/lectureId/
                                     platformSessionId/letiNumber/partOfSplit/durationSec/
                                     usedDeck/deckCoverageNote/media/transcript/platformArtifacts）
      sessions/<slug>/slides.json    该节次的页图（raw 层扁平结构：startSec/timecodeSource/
                                     rawTimecode/contentType/...）
      pdf.json                       顶层 pdf 块（pageCount/sha256/author/createdAt/isSelection/...）
      pdf/slides.json                PDF 渲染页图（source=pdf-render）
      projections.json               **人工**放映记录表：pdfPage -> [projection, ...]
      page_no.json                   **人工**页脚对照表：pdfPage -> {expected,fullOcr,footerOcr,status,note,footerUnit}
      unarchived.json                课堂用到但尚未归档的材料 [{what,evidence,impact}]
      source_register.json           待写入 Source Register 的行（id 留空）
      warnings.json                  需要人工判断的事实

三条硬规则
----------

1. **时间码一律用解码时间码 / 平台时间码换算成秒**，绝不用容器时间戳推算。
   ``projections[].startSec`` 与 ``timecodeSource`` + ``rawTimecode`` 成对出现，防止单位混淆。
2. **页脚不是页码，是小节 / 版式编号**（L08 实测 p26–p31 与 p33 页脚都是 10）。
   所以 ``verified`` 只要求**两路读数一致**；跨页重复**不影响判定**。
   只有明显偏离相邻页序列（L08 p24 页脚 42，前后页 08/09）才判 ``anomalous``。
3. **空 ``projections`` = 未见对应讲解**（只存在于课件里）——这是判断
   「课件存在 ≠ 课堂讲过」的唯一依据。跨链对齐（快照轨 ↔ PDF 页）只能由人工
   ``--projection-map`` 给出，**脚本不猜**。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters"))
import _common as C  # noqa: E402


# --------------------------------------------------------------------------
# 读取 raw/
# --------------------------------------------------------------------------

def load_work(work: Path) -> dict:
    raw = C.raw_dir(work)
    if not raw.exists():
        raise SystemExit(f"找不到 {raw}：先跑 adapters/*.py 采集，或用 --help 看 raw/ 契约。")

    data: dict = {"raw": raw, "sessions": [], "warnings": [], "unarchived": [],
                  "register": [], "slides": [], "pdf": None, "source": {}}

    src_path = raw / "source.json"
    if src_path.exists():
        data["source"] = C.read_json(src_path)

    sdir = C.sessions_dir(work)
    if sdir.exists():
        for d in sorted(p for p in sdir.iterdir() if p.is_dir()):
            meta_path = d / "session.json"
            if not meta_path.exists():
                data["warnings"].append(f"sessions/{d.name}/ 没有 session.json，已跳过")
                continue
            meta = C.read_json(meta_path)
            meta["_dir"] = d
            slides_path = d / "slides.json"
            meta["_slides"] = C.read_json(slides_path) if slides_path.exists() else []
            if not meta["_slides"]:
                data["warnings"].append(f"节次「{meta.get('label', d.name)}」没有 slides.json（本节无页图）")
            data["sessions"].append(meta)

    for key, target in (("warnings.json", "warnings"), ("unarchived.json", "unarchived"),
                        ("source_register.json", "register")):
        p = raw / key
        if p.exists():
            value = C.read_json(p)
            data[target] = value if isinstance(value, list) else []

    pdf_meta = raw / "pdf.json"
    if pdf_meta.exists():
        data["pdf"] = C.read_json(pdf_meta)
    pdf_slides = raw / "pdf" / "slides.json"
    if pdf_slides.exists():
        data["pdf_slides"] = C.read_json(pdf_slides)
    else:
        data["pdf_slides"] = []
    return data


def order_sessions(sessions: list[dict], explicit: str | None) -> list[dict]:
    """节次顺序 = ``--session-order`` 显式指定，否则 (partOfSplit, label)。"""
    if explicit:
        wanted = [s.strip() for s in explicit.split(",") if s.strip()]
        by_label = {s.get("label"): s for s in sessions}
        out = []
        for w in wanted:
            if w not in by_label:
                raise SystemExit(f"--session-order 里的「{w}」不在 raw/sessions/ 中："
                                 f"现有 {list(by_label)}")
            out.append(by_label[w])
        for s in sessions:
            if s not in out:
                out.append(s)
        return out
    return sorted(sessions, key=lambda s: (s.get("partOfSplit") or 999, str(s.get("label") or "")))


def resolve_session_index(value, sessions: list[dict]) -> int | None:
    """``--projection-map`` 里的 ``session`` 可以是 1-based 序号或节次标签。"""
    if value is None:
        return None
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        idx = int(value)
        if 1 <= idx <= len(sessions):
            return idx
        return None
    for i, s in enumerate(sessions, 1):
        if s.get("label") == value:
            return i
    return None


# --------------------------------------------------------------------------
# 幻灯片归并
# --------------------------------------------------------------------------

def merge_key(slide: dict) -> str:
    if slide.get("pdfPage"):
        return f"pdf:{slide['pdfPage']}"
    return f"file:{Path(str(slide.get('file') or '')).name}"


def to_manifest_slide(raw_slide: dict, session_index: int | None, extra_projs: list[dict],
                      file_prefix: str | None, default_unit: str | None,
                      default_scope: str | None) -> dict:
    """raw 层扁平页 -> manifest 的 slide（含 ``projections[]``）。"""
    projs: list[dict] = []
    if raw_slide.get("startSec") is not None and session_index:
        projs.append(C.make_projection(
            session_index, raw_slide.get("startSec"),
            timecode_source=raw_slide.get("timecodeSource"),
            raw_timecode=raw_slide.get("rawTimecode"),
        ))
    projs.extend(extra_projs)

    file_str = str(raw_slide.get("file") or "")
    if file_prefix and file_str:
        # --file-prefix = 归档目录；页图文件名沿用 raw 里的 basename
        file_str = f"{file_prefix.rstrip('/')}/{Path(file_str).name}"

    fields = {
        "pdfPage": raw_slide.get("pdfPage"),
        "title": raw_slide.get("title"),
        "contentType": raw_slide.get("contentType") or "unknown",
        "isPureImagePage": raw_slide.get("isPureImagePage"),
        "textExtractionIssue": raw_slide.get("textExtractionIssue"),
        "duplicateOf": raw_slide.get("duplicateOf"),
        "ocrText": raw_slide.get("ocrText"),
        "footerUnit": raw_slide.get("footerUnit") or default_unit,
        "pageNoScope": raw_slide.get("pageNoScope") or default_scope,
        "pageNoExpected": raw_slide.get("pageNoExpected"),
        "pageNoFullOcr": raw_slide.get("pageNoFullOcr"),
        "pageNoFooterOcr": raw_slide.get("pageNoFooterOcr"),
        "pageNoNormalized": raw_slide.get("pageNoNormalized"),
        "pageNoStatus": raw_slide.get("pageNoStatus"),
        "pageNoNote": raw_slide.get("pageNoNote"),
    }
    return C.make_slide(raw_slide.get("index") or 1, file_str,
                        raw_slide.get("source") or "unknown", projections=projs, **fields)


def collect_slides(data: dict, args) -> tuple[list[dict], list[str]]:
    """把各节次页图 + PDF 页图归并成 manifest 的 ``slides[]``。"""
    warnings: list[str] = []
    projections_map = C.read_json(Path(args.projection_map)) if args.projection_map else {}
    page_no_map = C.read_json(Path(args.page_no_map)) if args.page_no_map else {}

    # 人工放映记录 -> {merge_key: [projection dict]}
    manual_projs: dict[str, list[dict]] = {}
    for key, entries in (projections_map or {}).items():
        bucket = manual_projs.setdefault(f"pdf:{key}" if str(key).isdigit() else f"file:{Path(str(key)).name}", [])
        for e in entries if isinstance(entries, list) else [entries]:
            # 接受 session（1-based 序号或节次标签）与 sessionIndex 两种写法
            si = resolve_session_index(e.get("session", e.get("sessionIndex")), data["sessions"])
            if si is None:
                warnings.append(f"--projection-map 的「{key}」指向未知 session "
                                f"{e.get('session', e.get('sessionIndex'))!r}，已跳过该条")
                continue
            bucket.append(C.make_projection(
                si, e.get("startSec"),
                timecode_source=e.get("timecodeSource") or "manual",
                raw_timecode=e.get("rawTimecode"),
                end_sec=e.get("endSec"),
                is_replay=bool(e.get("isReplay")),
            ))

    merged: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []

    def add(raw_slide: dict, session_index: int | None):
        source = raw_slide.get("source") or "unknown"
        key = (source, merge_key(raw_slide))
        extra = list(manual_projs.get(merge_key(raw_slide), []))
        if key in merged:
            # 同一来源、同一物理页（跨节次 / 跨文件）-> 合并放映记录
            existing = merged[key]
            have = {(p.get("sessionIndex"), p.get("startSec")) for p in existing.get("projections", [])}
            for p in extra:
                if (p.get("sessionIndex"), p.get("startSec")) not in have:
                    existing.setdefault("projections", []).append(p)
                    have.add((p.get("sessionIndex"), p.get("startSec")))
            return
        merged[key] = to_manifest_slide(raw_slide, session_index, extra, args.file_prefix,
                                        args.footer_unit, args.page_no_scope)
        order.append(key)

    for i, sess in enumerate(data["sessions"], 1):
        for raw_slide in sess.get("_slides", []):
            add(raw_slide, i)
    for raw_slide in data.get("pdf_slides", []):
        add(raw_slide, None)

    # 页脚对照表（人工）覆盖到对应页
    for key, pn in (page_no_map or {}).items():
        target_key = f"pdf:{key}" if str(key).isdigit() else f"file:{Path(str(key)).name}"
        hits = [k for k in order if k[1] == target_key]
        if not hits:
            warnings.append(f"--page-no-map 的「{key}」在 slides 里找不到对应页，已跳过")
            continue
        for k in hits:
            slide = merged[k]
            for src_key, dst_key in (("expected", "pageNoExpected"), ("fullOcr", "pageNoFullOcr"),
                                     ("footerOcr", "pageNoFooterOcr"), ("note", "pageNoNote"),
                                     ("footerUnit", "footerUnit"), ("pageNoScope", "pageNoScope")):
                if pn.get(src_key) is not None:
                    slide[dst_key] = pn[src_key]
            if pn.get("status"):
                slide["pageNoStatus"] = pn["status"]
            norm = C.normalize_page_no(pn.get("footerOcr") or pn.get("fullOcr") or pn.get("expected"))
            if norm:
                slide["pageNoNormalized"] = norm

    # 排序：PDF 渲染页按阅读页在前，其余按 (sessionIndex, startSec)
    def sort_key(k):
        s = merged[k]
        if s.get("source") == "pdf-render":
            return (0, s.get("pdfPage") or 0, 0.0)
        first = (s.get("projections") or [{}])[0]
        return (1, first.get("sessionIndex") or 999, first.get("startSec") or 0.0)

    slides = [merged[k] for k in sorted(order, key=sort_key)]
    for i, s in enumerate(slides, 1):
        s["index"] = i
    return slides, warnings


# --------------------------------------------------------------------------
# 页脚状态判定
# --------------------------------------------------------------------------

def compute_page_no(slides: list[dict], default_scope: str | None) -> list[str]:
    """判定 ``pageNoStatus``。

    规则（按优先级）：

    * 人工已给 ``pageNoStatus`` -> 保留（人工判断优先）
    * 该页无页码体系（scope=none）或非课件画面（desktop）-> ``not-applicable``
    * 两路读数都没有 -> ``missing``
    * 只有一路有读数，或两路归一化后不一致 -> ``mismatch``
    * 两路一致：与**相邻页**序列明显不符 -> ``anomalous``；否则 ``verified``

    **页脚跨页重复不算异常**——页脚是小节 / 版式编号（L08 实测 p26–p31 与 p33 都是 10）。
    ``pageNoScope=section`` 时编号**不单调**（小节会重启，p33=10 而 p32=11、p34=12），
    因此只抓「超出同单元最高小节号 +1 且不被任何同单元页共享」的离群值（L08 只有 p24=42）；
    ``pageNoScope=page``（真页码）才用相邻页单调性。

    实现上分两趟：第一趟把全部页的 ``pageNoNormalized`` 算出来，第二趟才判状态。
    （一趟做完会漏判：判断第 N 页时，第 N+1 页的归一化值还没算出来，L08 p24 的
    ``anomalous`` 就是这样被漏掉的。）
    """
    warnings: list[str] = []
    annotated_notes: list[str] = []

    # 第一趟：归一化（必须全部先算完，邻居比较才有右侧数据）
    for s in slides:
        if not s.get("pageNoNormalized"):
            norm = C.normalize_page_no(s.get("pageNoFooterOcr") or s.get("pageNoFullOcr"))
            if norm:
                s["pageNoNormalized"] = norm

    # 第二趟：判状态
    for s in slides:
        norm = s.get("pageNoNormalized")
        if not norm:
            norm = C.normalize_page_no(s.get("pageNoFooterOcr") or s.get("pageNoFullOcr"))
            if norm:
                s["pageNoNormalized"] = norm

        if s.get("pageNoStatus"):
            if s["pageNoStatus"] in ("anomalous", "mismatch", "missing") and not s.get("pageNoNote"):
                warnings.append(f"index={s['index']}（p{s.get('pdfPage')}）status="
                                f"{s['pageNoStatus']} 但缺 pageNoNote（schema 要求写明原始读数与判断）")
            continue

        scope = s.get("pageNoScope") or default_scope
        full, foot = s.get("pageNoFullOcr"), s.get("pageNoFooterOcr")
        n_full, n_foot = C.normalize_page_no(full), C.normalize_page_no(foot)

        if scope == "none" or s.get("contentType") == "desktop":
            s["pageNoStatus"] = "not-applicable"
            continue
        if not n_full and not n_foot:
            s["pageNoStatus"] = "missing"
            s.setdefault("pageNoNote", "无页脚数字；按设计如此，保留原样")
            continue
        if not n_full or not n_foot:
            s["pageNoStatus"] = "mismatch"
            s.setdefault("pageNoNote",
                         f"只有一路读到页脚（整帧 OCR={full!r}、页脚裁切={foot!r}），"
                         f"按待核实保留，不补猜")
            warnings.append(f"index={s['index']}（p{s.get('pdfPage')}）页脚只有一路读数，判 mismatch")
            continue
        if n_full != n_foot:
            s["pageNoStatus"] = "mismatch"
            s.setdefault("pageNoNote",
                         f"两路读数不一致：整帧 OCR={full!r}（归一化 {n_full}）、"
                         f"页脚裁切={foot!r}（归一化 {n_foot}）；按待核实保留")
            warnings.append(f"index={s['index']}（p{s.get('pdfPage')}）页脚两路不一致：{n_full} vs {n_foot}")
            continue

        # 两路一致 -> 判是否明显偏离所属序列
        # ⚠️ 页脚是**小节 / 版式编号**（pageNoScope=section）时**不是单调递增的**：
        #    同一单元里小节会重启（L08 p33 页脚 10，而 p32=11、p34=12），
        #    也会被多页共享（p26–p31 与 p33 都是 10）。这些**都是正常的**。
        #    所以 section 作用域下只抓「超出该单元最高小节号 +1 且该值不被任何同单元页共享」
        #    这类真正的离群值（L08 只有 p24 页脚 42，而该单元其他页是 02–13）。
        #    page 作用域（真页码）才用相邻页单调性判断。
        unit = s.get("footerUnit")
        others = [int(x["pageNoNormalized"]) for x in slides
                  if x is not s and x.get("footerUnit") == unit and x.get("pageNoNormalized")]
        v = int(norm)
        anomalous = None
        # 带版式标注的页脚（如「实验心理学 11」「实验心理学 06」）来自**另一份课件版式**，
        # 其编号与纯数字页脚不属于同一序列，不能拿来比大小 —— 只按「两路一致」判 verified，
        # 并把「尚未归一到一个序列」作为需要人工判断的事实记进 warnings。
        annotated = not str(full).strip().isdigit() or not str(foot).strip().isdigit()
        if annotated:
            annotated_notes.append(
                f"p{s.get('pdfPage')} 页脚带版式标注（整帧 OCR={full!r}），来自另一份课件版式，"
                f"尚未归一到一个序列，属人工判断")
        elif scope == "page":
            ordered = sorted(((x.get("pdfPage") or x["index"]), int(x["pageNoNormalized"]))
                             for x in slides if x.get("pageNoNormalized"))
            seq = [val for _, val in ordered]
            pos = [i for i, (pp, _) in enumerate(ordered) if pp == (s.get("pdfPage") or s["index"])]
            if pos and seq:
                i = pos[0]
                prev_v = seq[i - 1] if i > 0 else None
                next_v = seq[i + 1] if i + 1 < len(seq) else None
                if (prev_v is not None and next_v is not None and prev_v < next_v
                        and not (prev_v <= v <= next_v)):
                    anomalous = (f"页脚显示 {norm}，而相邻页序列为 {prev_v:02d}–{next_v:02d}")
        else:
            hi = max(others) if others else None
            if others and v not in others and v > hi + 1:
                anomalous = (f"页脚显示 {norm}，超出该单元其他页的小节编号范围 "
                             f"{min(others):02d}–{hi:02d}，且无任何同单元页共享该编号")

        if anomalous:
            s["pageNoStatus"] = "anomalous"
            s.setdefault("pageNoNote",
                         f"{anomalous}，判断为排版笔误；按 AGENTS.md §5 保留原读数、不静默改名，"
                         f"引用以 PDF 阅读页 p{s.get('pdfPage')} 为准")
            warnings.append(f"index={s['index']}（p{s.get('pdfPage')}）{anomalous}，判 anomalous（按原样保留）")
        else:
            s["pageNoStatus"] = "verified"

    if annotated_notes:
        warnings.append("带版式标注的页脚（来自另一份课件版式，尚未归一到一个序列，属人工判断）："
                        + "；".join(annotated_notes))
    return warnings


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------

def build(work: Path, args) -> dict:
    data = load_work(work)
    warnings: list[str] = list(data["warnings"])

    # raw/ 契约里的两个人工事实文件：未显式传参时自动发现
    raw = C.raw_dir(work)
    if not args.projection_map and (raw / "projections.json").exists():
        args.projection_map = str(raw / "projections.json")
        warnings.append("使用 raw/projections.json 作为人工放映记录表（未显式传 --projection-map）")
    if not args.page_no_map and (raw / "page_no.json").exists():
        args.page_no_map = str(raw / "page_no.json")
        warnings.append("使用 raw/page_no.json 作为人工页脚对照表（未显式传 --page-no-map）")

    sessions = order_sessions(data["sessions"], args.session_order)

    slides, warns = collect_slides(data, args)
    warnings += warns
    if not slides:
        warnings.append("slides 为空：既没有节次页图也没有 PDF 页图")
    warnings += compute_page_no(slides, args.page_no_scope)

    src = data.get("source") or {}
    course = src.get("course") or {}
    course_id = args.course_id or course.get("id") or "unknown"
    if course_id == "unknown":
        warnings.append("Canvas 课程 id 未确认：course.id 写 unknown，未编造")
    platform = args.platform or src.get("platform") or ("pdf" if data.get("pdf") else "unknown")

    out_sessions = []
    for i, s in enumerate(sessions, 1):
        entry = {k: v for k, v in s.items() if not k.startswith("_")}
        entry.setdefault("label", f"session-{i}")
        out_sessions.append(entry)

    manifest = {
        "schemaVersion": 1,
        "source": {
            "platform": platform,
            "course": {
                "id": str(course_id),
                "name": args.course_name or course.get("name") or str(course_id),
                **({"code": args.course_code or course.get("code")}
                   if (args.course_code or course.get("code")) else {}),
                **({"extra": course["extra"]} if course.get("extra") else {}),
            },
            "capturedAt": src.get("capturedAt") or C.now_iso(),
            **({"platformSubtitles": src["platformSubtitles"]} if src.get("platformSubtitles") else {}),
            "sessions": out_sessions,
        },
        "slides": slides,
    }
    if data.get("pdf"):
        manifest["pdf"] = data["pdf"]
    if data.get("register"):
        manifest["sourceRegisterEntries"] = data["register"]
    if data.get("unarchived"):
        manifest["unarchivedMaterials"] = data["unarchived"]

    # 媒体政策：archived=false 时必须记「未归档」而不是失效链接
    for i, s in enumerate(out_sessions, 1):
        media = s.get("media")
        if media and not media.get("archived"):
            if media.get("videoPath"):
                media["videoPath"] = None
            if not media.get("note"):
                media["note"] = "原始媒体未归档：仅作临时处理输入，处理完成后清理"
            warnings.append(f"节次「{s.get('label')}」原始媒体未归档（archived=false），已清空 videoPath")
        if s.get("transcript") and s["transcript"].get("speakerLabelsAreInferred") is not True:
            s["transcript"]["speakerLabelsAreInferred"] = True
            warnings.append(f"节次「{s.get('label')}」说话人标签是自动推断，已强制 "
                            f"speakerLabelsAreInferred=true")

    # 空 projections = 未见对应讲解：这是「课件存在 ≠ 课堂讲过」的唯一依据
    n_empty = sum(1 for s in slides if not s.get("projections"))
    if n_empty:
        warnings.append(f"{n_empty}/{len(slides)} 页 projections 为空 = 未见对应讲解（只存在于课件里）")

    manifest["warnings"] = list(dict.fromkeys(warnings))
    return manifest


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

def validate_schema(manifest: dict, schema_path: Path) -> tuple[bool, list[str], str]:
    """用 jsonschema 自校验；不可用时回退内置最小校验（并说明是降级）。"""
    try:
        import jsonschema
    except ImportError:
        return minimal_validate(manifest)
    if not schema_path.exists():
        return False, [f"找不到 schema：{schema_path}"], "jsonschema（缺 schema 文件）"
    schema = C.read_json(schema_path)
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda e: list(e.path))
    msgs = [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors[:40]]
    return (not msgs), msgs, "jsonschema"


def minimal_validate(manifest: dict) -> tuple[bool, list[str], str]:
    """内置最小校验（jsonschema 不可用时的降级路径，覆盖主要枚举与必填）。"""
    errs: list[str] = []
    for key in ("schemaVersion", "source", "slides", "warnings"):
        if key not in manifest:
            errs.append(f"<root>: 缺必填 {key}")
    if manifest.get("schemaVersion") != 1:
        errs.append("schemaVersion: 必须为 1")
    src = manifest.get("source") or {}
    for key in ("platform", "course", "capturedAt", "sessions"):
        if key not in src:
            errs.append(f"source: 缺必填 {key}")
    if src.get("platform") not in ("canvas", "chaoxing", "pdf", "unknown"):
        errs.append(f"source.platform: 非法值 {src.get('platform')!r}")
    for i, s in enumerate(manifest.get("slides") or [], 1):
        if s.get("index") != i:
            errs.append(f"slides[{i-1}].index: 应为 {i}，实际 {s.get('index')}（必须连续）")
        if s.get("source") not in C.SOURCES:
            errs.append(f"slides[{i-1}].source: 非法值 {s.get('source')!r}")
        if s.get("contentType") and s["contentType"] not in C.CONTENT_TYPES:
            errs.append(f"slides[{i-1}].contentType: 非法值 {s['contentType']!r}")
        if s.get("pageNoStatus") and s["pageNoStatus"] not in C.PAGE_NO_STATUSES:
            errs.append(f"slides[{i-1}].pageNoStatus: 非法值 {s['pageNoStatus']!r}")
        if s.get("pageNoScope") and s["pageNoScope"] not in C.PAGE_NO_SCOPES:
            errs.append(f"slides[{i-1}].pageNoScope: 非法值 {s['pageNoScope']!r}")
        for j, p in enumerate(s.get("projections") or []):
            if "sessionIndex" not in p or "startSec" not in p:
                errs.append(f"slides[{i-1}].projections[{j}]: 缺 sessionIndex / startSec")
            if p.get("timecodeSource") and p["timecodeSource"] not in C.TIMECODE_SOURCES:
                errs.append(f"slides[{i-1}].projections[{j}].timecodeSource: 非法值")
            if p.get("startMmss") and not __import__("re").match(r"^[0-9]{1,2}:[0-9]{2}$", p["startMmss"]):
                errs.append(f"slides[{i-1}].projections[{j}].startMmss: 格式应为 M:SS")
    if not src.get("sessions") and "pdf" not in manifest:
        errs.append("allOf: source.sessions 为空时必须提供 pdf")
    return (not errs), errs[:40], "内置最小校验（jsonschema 不可用，已降级）"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_manifest.py",
        description="把 raw/ 汇总成符合 manifest.schema.json 的 manifest.json。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="空 projections = 未见对应讲解；页脚跨页重复不是异常。",
    )
    p.add_argument("work", help="工作目录（含 raw/）")
    p.add_argument("-o", "--out", default=None, help="输出路径（默认 <work>/manifest.json）")
    p.add_argument("--schema", default=str(C.MANIFEST_SCHEMA), help="manifest schema 路径")

    p.add_argument("--projection-map", default=None,
                   help="人工放映记录表 JSON：{\"9\": [{\"session\":1,\"startSec\":2953,"
                        "\"timecodeSource\":\"platform-imageSeekTime-sec\",\"rawTimecode\":\"2953\"}]}")
    p.add_argument("--page-no-map", default=None,
                   help="人工页脚对照表 JSON：{\"24\": {\"expected\":\"42\",\"fullOcr\":\"42\","
                        "\"footerOcr\":\"42\",\"status\":\"anomalous\",\"note\":\"…\"}}")
    p.add_argument("--footer-unit", default=None, help="默认栏目条 / 单元名")
    p.add_argument("--page-no-scope", choices=list(C.PAGE_NO_SCOPES), default=None,
                   help="页脚编号作用域（L08 是 section）")
    p.add_argument("--file-prefix", default=None, help="slides[].file 的归档路径前缀")
    p.add_argument("--session-order", default=None, help="显式指定节次顺序，如 \"L08 第15节 1355,L08 第16节 1450\"")

    p.add_argument("--platform", choices=["canvas", "chaoxing", "pdf", "unknown"], default=None)
    p.add_argument("--course-id", default=None)
    p.add_argument("--course-name", default=None)
    p.add_argument("--course-code", default=None)

    p.add_argument("--no-validate", action="store_true", help="跳过 schema 校验（**不推荐**）")
    p.add_argument("--strict-warnings", action="store_true", help="warnings 非空即退出 1")
    p.add_argument("--print-summary", action="store_true", help="打印统计摘要")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    work = Path(args.work).expanduser().resolve()
    manifest = build(work, args)

    try:
        C.assert_no_secrets(manifest, "manifest.json")
    except C.SecretLeak as exc:
        print(str(exc), file=sys.stderr)
        print("已拒绝写盘（铁律：签名 URL / cookie / token 不入档）。", file=sys.stderr)
        return 2

    ok, errors, engine = (True, [], "skipped")
    if not args.no_validate:
        ok, errors, engine = validate_schema(manifest, Path(args.schema))
        # schema 表达不了「index 必须连续」（只在 description 里），所以**始终**补跑内置最小校验，
        # 两边的错误合并。jsonschema 可用时它是主力，内置校验只补它抓不到的那几条。
        ok2, errors2, engine2 = minimal_validate(manifest)
        if not ok2:
            ok = False
            extra = [e for e in errors2 if e not in errors]
            if extra:
                errors = errors + extra
                engine = f"{engine} + 内置最小校验（补 schema 表达不了的约束，如 index 连续）"

    out = Path(args.out).expanduser().resolve() if args.out else work / "manifest.json"
    C.write_json(out, manifest)

    C.banner(f"manifest.json 已写出：{out}")
    print(f"校验引擎：{engine}")
    print(f"platform={manifest['source']['platform']}  "
          f"sessions={len(manifest['source']['sessions'])}  "
          f"slides={len(manifest['slides'])}  "
          f"projections={sum(len(s.get('projections', [])) for s in manifest['slides'])}")
    if args.print_summary:
        from collections import Counter
        print(f"  source:      {dict(Counter(s['source'] for s in manifest['slides']))}")
        print(f"  contentType: {dict(Counter(s.get('contentType') for s in manifest['slides']))}")
        print(f"  pageNoStatus:{dict(Counter(s.get('pageNoStatus') for s in manifest['slides']))}")
        print(f"  空 projections（未见对应讲解）："
              f"{sum(1 for s in manifest['slides'] if not s.get('projections'))} 页")
        if manifest.get("pdf"):
            print(f"  pdf: {manifest['pdf'].get('pageCount')} 页 "
                  f"isSelection={manifest['pdf'].get('isSelection')}")
        print(f"  unarchivedMaterials: {len(manifest.get('unarchivedMaterials', []))}")
        print(f"  sourceRegisterEntries: {len(manifest.get('sourceRegisterEntries', []))}")

    print(f"\nwarnings（需要人工判断的事实）{len(manifest['warnings'])} 条：")
    for w in manifest["warnings"]:
        print(f"  - {w}")

    if not ok:
        print(f"\n[schema 校验失败] {len(errors)} 条：", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"\n[schema 校验通过] {engine}")
    if args.strict_warnings and manifest["warnings"]:
        print("[strict-warnings] warnings 非空，按严格模式退出 1", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
