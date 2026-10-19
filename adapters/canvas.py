#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SJTU Canvas 课堂回放采集适配器（L0 采集层）。

**只做编排与契约转换**：课件下载、场景抽取、OCR、页脚页码、去重、转写、说话人分离
全部 subprocess 调用 ``better-awesome-sjtu-canvas-ppt`` 的既有脚本，**不复制它们的实现**。

用法
----
::

    # ② 发现节次（需要浏览器已登录 SJTU + opencli 可用）
    python canvas.py --list --course <COURSE_ID> [--tecl-id <TECL_ID>]
    python canvas.py --probe-ppt --course <COURSE_ID>

    # ③ 采集（每个平台节次跑一次，不合并）
    python canvas.py --course <COURSE_ID> --session "L08 第15节 1355" --out <work> \\
        --ppt-json <captured_ppt.json> --leti 15 --part-of-split 1 \\
        --platform-session-id 179741 --class-date 2026-10-13 \\
        --used-deck "第9次课.pptx" --deck-coverage-note "0:00–35:07 未使用 S84"
    python canvas.py --course <COURSE_ID> --session "L08 第16节 1450" --out <work> \\
        --video "L08 第16节 stream2 (record5).mp4" --leti 16 --part-of-split 2

路径 A 的真实性质（心理库 L08 实测，2026-10-13）
------------------------------------------------
平台课件轨返回的**不是干净原图，而是屏幕快照轨**：``data.data.docList[]``，字段
``imageId / imageSeekTime / imageSnapshotTime / imageUrl / ocrText / pptDocId /
showFlag / sliceTaskId / taskId / textKeywordList``。三个必须处理的实测事实：

1. **冗余极重**：第15节 68 条 + 第16节 63 条 = 131 条快照，而 S84 只有 35 个 PDF 阅读页。
   动画构建帧 + 重复帧占绝大多数 → **即使走路径 A，去重也是必需的**
   （用上游 ``dedupe_slides.py``，文本 Jaccard 阈值 0.82）。
2. **含教师桌面与应用窗口**：第15节最早几条 OCR 是教师桌面（回收站 / 东方财富证券 /
   Chrome / 微信 / VLC），188 秒起出现 ``PowerPoint 文件 开始 插入 绘图 设计 切换``，
   即**把 PowerPoint 编辑窗口本身也捕获了** → 标 ``contentType: "desktop"``，
   按「素材」命名。**不是课件页。**
3. **``showFlag`` 不可用**：68/68 与 63/63 条全为 ``true``，不代表「是否放映」，
   **不能用作过滤条件**；``pptDocId`` 与 ``imageId`` 也都全不重复，没有现成的去重键。

``imageSeekTime`` 实测 max = 2953 / 3210，两节时长 3308.6 / 3289.0 秒 → **单位是秒**，
记 ``timecodeSource: "platform-imageSeekTime-sec"`` 并保留 ``rawTimecode``。

与 PDF 页图的关系
-----------------
平台快照轨与「教师 PDF 渲染页图」（``source: "pdf-render"``）是**两条独立证据链**：
前者有时间码、有冗余、含桌面；后者页码确定、无时间码。本适配器**绝不用快照轨的
时间码去覆盖 PDF 页图**；跨链对齐只能由人工在 ``build_manifest.py --projection-map``
里显式给出。

被调用的上游脚本**真实 CLI**（读源码实测，非猜测）::

    download_ppt.py        <json_file> <output_dir>
    extract_ppt_scenes.py  <video> <outdir> <label> [--thresh 0.02] [--quality 2]
    extract_ppt_frames.py  <video> <outdir> <label> [--interval 2.0] [--thresh 0.02]
                           [--min-dur 4.0] [--width 480]
    ocr_slides.py          <manifest> [--outdir DIR] [--gpu] [--lang ch_sim,en]
    read_slide_pageno.py   <manifest> [--gpu] [--out FILE] [--bottom 0.10] [--right 0.30]
    dedupe_slides.py       <ocr_json> [--thresh 0.80] [--out kept.json]
    transcribe_class.py    <wav> <outdir> <label> [--model large-v3] [--device cuda]
                           [--speakers 2] [--language] [--chunk 600]
    diarize_transcript.py  <transcript_json> [--wav WAV] [--speakers 2]
                           [--backend xvector|mfcc] [--stride 1] [--outdir DIR]
                           [--teacher-label L] [--student-label L]

只读纪律
--------
本适配器**不播放视频、不触发心跳、不点任务点**；不向平台发任何请求。
平台侧取数由学习者在已登录浏览器里完成（opencli 或手工），本脚本只消费其产物。

签名 URL 处理
-------------
平台图片直链只在**本次运行内存与 ``<work>/_tmp_urls/`` 临时文件**里存在，用完立即删除；
``raw/`` 下任何产物落盘前都过 :func:`_common.assert_no_secrets`，URL 字段一律剥离。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

# 时间码口径：旧接口 createSec 是毫秒，新清单 imageSeekTime 是秒
MS_KEYS = ("createSec", "createSecMs", "s")
SEC_KEYS = ("imageSeekTime", "imageSeekTimeSec", "startSec")
IMG_KEYS = ("imageUrl", "pptImgUrl", "pptImgURL", "imgUrl", "url")

PROBE_PPT_JS = (
    "fetch('/jy-application-resourcemanage/v1/course/ai/getAiFunctionShow',"
    "{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify({courseId:<COURSE_ID>})}).then(r=>r.text())"
)
LIST_JS = (
    "fetch('/jy-application-resourcemanage/v1/subject_vod_list_new?page.pageIndex=1"
    "&page.pageSize=100&teclIds=<TECL_ID>&page.orders%5B0%5D.asc=false"
    "&page.orders%5B0%5D.field=courBeginTime&schoolOpenStatusFlag=false',"
    "{credentials:'include'}).then(r=>r.json())"
    ".then(d=>JSON.stringify(d.data.records.map(r=>({id:r.id,leti:r.letiNumber,begin:r.courBeginTime}))))"
)
VOD_URLS_JS = (
    "fetch('/jy-application-resourcemanage/v1/course_vod_urls_new?courseId=<ID>',"
    "{credentials:'include'}).then(r=>r.json())"
    ".then(d=>JSON.stringify(d.data.courseVodViewList.map(v=>v.url)))"
)
INJECT_JS = (
    "window.__pptCapture=[];"
    "var o=XMLHttpRequest.prototype.open,s=XMLHttpRequest.prototype.send;"
    "XMLHttpRequest.prototype.open=function(m,u){this.__pptUrl=u;return o.apply(this,arguments)};"
    "XMLHttpRequest.prototype.send=function(){var t=this;"
    "this.addEventListener('load',function(){if(t.__pptUrl&&t.__pptUrl.indexOf('ppt')>-1)"
    "window.__pptCapture.push({url:t.__pptUrl,body:t.responseText})});return s.apply(this,arguments)};"
    "var f=window.fetch;window.fetch=function(i,n){var u=typeof i==='string'?i:(i&&i.url?i.url:'');"
    "return f.apply(this,arguments).then(function(r){if(u.indexOf('ppt')>-1){var c=r.clone();"
    "c.text().then(function(b){window.__pptCapture.push({url:u,body:b})})}return r})};"
)

# --------------------------------------------------------------------------
# 非课件画面特征词（教师桌面 / 应用窗口 / 编辑器功能区）
# 依据：心理库 L08 实测 ocrText —— 回收站、东方财富证券、Chrome、微信、VLC、
#       「PowerPoint 文件 开始 插入 绘图 设计 切换」、末尾「上海交通大学 此电脑 WS508…」
# --------------------------------------------------------------------------
NON_SLIDE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Windows 桌面图标", re.compile(r"回收站|此电脑|网上邻居|文件资源管理器|控制面板")),
    ("PowerPoint 编辑窗口（功能区）",
     re.compile(r"(文件\s*开始\s*插入|开始\s*插入\s*绘图|插入\s*绘图\s*设计|设计\s*切换\s*动画|幻灯片放映\s*审阅\s*视图)")),
    ("其它编辑器 / 阅读器", re.compile(r"Adobe\s*Acrobat|Microsoft\s*Word|WPS|Notepad|记事本|写字板")),
    ("聊天 / 传输工具", re.compile(r"文件传输助手|微信|钉钉|企业微信|飞书")),
    ("浏览器窗口", re.compile(r"Google\s*Chrome|Microsoft\s*Edge|Firefox|新标签页")),
    ("播放器窗口", re.compile(r"\bVLC\b|PotPlayer|Windows\s*Media\s*Player")),
    ("券商 / 行情终端", re.compile(r"东方财富|同花顺|证券|行情")),
    ("远程 / 桌面共享标识", re.compile(r"上海交通大学\s*此电脑|远程桌面|屏幕共享|正在共享")),
]

# 这些词出现时**不能**判为桌面（课件里也可能出现「网络」「微信」这类词）
SLIDE_RESCUE = re.compile(r"心理学|心理学史|PSYCHOLOGY|Chapter|CHAPTER|Module|UNIT|Checkpoint|"
                          r"讨论|习题|思考题|参考文献")


def classify_snapshot(ocr_text: str | None) -> tuple[str, str | None]:
    """按平台 ``ocrText`` 判断快照的 ``contentType``。

    返回 ``(content_type, reason)``：命中非课件特征词且不含课件保护词 → ``"desktop"``；
    文本为空时保守返回 ``"unknown"``（**不猜**成 slide）。
    """
    text = (ocr_text or "").strip()
    if not text:
        return "unknown", None
    if SLIDE_RESCUE.search(text):
        return "slide", None
    for label, pat in NON_SLIDE_PATTERNS:
        if pat.search(text):
            return "desktop", label
    return "slide", None


# --------------------------------------------------------------------------
# 平台产物规范化（纯本地，可离线测试）
# --------------------------------------------------------------------------

def _first(d: dict, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def find_snapshot_records(obj) -> list[dict]:
    """递归找出快照记录列表。

    实测响应体是 ``data.data.docList[]``；旧接口是 ``data[]``；也可能直接是数组。
    这里不写死层级，找**第一个含时间码字段的 dict 列表**。
    """
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and (
                _first(obj[0], MS_KEYS) is not None or _first(obj[0], SEC_KEYS) is not None):
            return [r for r in obj if isinstance(r, dict)]
        for v in obj:
            found = find_snapshot_records(v)
            if found:
                return found
        return []
    if isinstance(obj, dict):
        for key in ("docList", "data", "records", "list", "slides"):
            if key in obj:
                found = find_snapshot_records(obj[key])
                if found:
                    return found
        for v in obj.values():
            found = find_snapshot_records(v)
            if found:
                return found
    return []


def normalize_capture(obj) -> tuple[list[dict], list[str]]:
    """平台课件轨清单 -> 规范化快照列表（含 ``contentType`` 分类与时间码口径）。

    产出的是 **raw 层扁平结构**（``startSec`` / ``timecodeSource`` / ``rawTimecode`` 在顶层），
    由 ``build_manifest.py`` 转成 manifest 的 ``projections[]``。
    """
    records = find_snapshot_records(obj)
    slides, warnings = [], []
    if not records:
        return slides, ["课件轨清单里没有找到快照记录（找的是含 imageSeekTime / createSec 的列表）"]

    for i, rec in enumerate(records, 1):
        ms = _first(rec, MS_KEYS)
        sec = _first(rec, SEC_KEYS)
        img = _first(rec, IMG_KEYS)
        if sec is not None:
            start = C.to_sec(sec)
            tc_source, raw = "platform-imageSeekTime-sec", str(sec)
        elif ms is not None:
            start = C.ms_to_sec(ms)
            tc_source, raw = "platform-createSec-ms", str(ms)
        else:
            start, tc_source, raw = None, None, None
            warnings.append(f"课件轨第 {i} 条没有时间码字段，startSec 置 null（待人工确认）")

        ocr_text = rec.get("ocrText") or None
        content_type, reason = classify_snapshot(ocr_text)
        slides.append({
            "index": i,
            "startSec": start,
            "startMmss": C.sec_to_mmss(start),
            "timecodeSource": tc_source,
            "rawTimecode": raw,
            "source": "platform-ppt-track",
            "contentType": content_type,
            "classificationReason": reason,
            "ocrText": ocr_text,
            "isPureImagePage": False if ocr_text else None,
            "_imageId": rec.get("imageId"),
            "_pptDocId": rec.get("pptDocId"),
            "_snapshotTime": rec.get("imageSnapshotTime"),
            "_showFlag": rec.get("showFlag"),
            "_imgUrl": img,          # 只在内存 / 临时文件里流转，落盘前剥离
        })

    if slides:
        if all(s["_showFlag"] is True for s in slides):
            warnings.append("平台 showFlag 在全部快照上都是 true，不代表「是否放映」，未用作过滤条件")
        n_desktop = sum(1 for s in slides if s["contentType"] == "desktop")
        n_unknown = sum(1 for s in slides if s["contentType"] == "unknown")
        if n_desktop:
            warnings.append(
                f"快照轨含 {n_desktop}/{len(slides)} 条教师桌面 / 应用窗口画面（contentType=desktop），不是课件页")
        if n_unknown:
            warnings.append(f"快照轨有 {n_unknown} 条没有平台 ocrText，contentType=unknown（未猜成 slide）")
        warnings.append("平台课件轨是**屏幕快照轨**（非干净原图），动画构建帧冗余重，已跑文本去重")
    return slides, warnings


def download_ppt_filename(ms: int) -> str:
    """复刻 ``download_ppt.py`` 的命名 ``MM_SS.jpg``（用于把下载结果对回快照）。"""
    total = int(ms) // 1000
    return f"{total // 60:02d}_{total % 60:02d}.jpg"


# --------------------------------------------------------------------------
# 路径 A：平台课件轨（下载 + 分类 + 去重）
# --------------------------------------------------------------------------

def download_track(slides: list[dict], outdir: Path, work: Path, dry_run: bool,
                   batch: int, force: bool) -> tuple[dict, list[str]]:
    """逐条调用 ``download_ppt.py``，并按 ``contentType`` 命名。

    ``batch=1``（默认）时每次只给一条并立即改名。原因：上游按 ``MM_SS`` 命名，
    **同一秒内的多张快照会互相覆盖**（L08 快照密集处一秒多条）。
    课件页命名 ``ppt p### (MMmSSs).jpg``；桌面/应用窗口命名 ``素材 — <原因> (MMmSSs).jpg``。
    """
    script = C.canvas_ppt_script("download_ppt.py")
    outdir.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(work) / "_tmp_urls"
    tmpdir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    downloaded: dict[str, str] = {}
    failed: list[int] = []
    n_slide = n_desktop = 0

    entries = [(s["index"], s) for s in slides if s.get("_imgUrl") and s["startSec"] is not None]
    groups = [entries] if batch <= 0 else [entries[i:i + batch] for i in range(0, len(entries), batch)]
    try:
        for gi, group in enumerate(groups, 1):
            if not group:
                continue
            feed = tmpdir / f"batch_{gi:04d}.json"
            C.write_json(feed, [{"s": str(int(round(s["startSec"] * 1000))), "u": s["_imgUrl"]}
                                for _, s in group])
            scratch = tmpdir / f"batch_{gi:04d}_out"
            C.run([C.python_exe(), script, feed, scratch], dry_run=dry_run)
            if dry_run:
                continue
            for idx, s in group:
                ms = int(round(s["startSec"] * 1000))
                src = scratch / download_ppt_filename(ms)
                if not src.exists():
                    failed.append(idx)
                    continue
                total = ms // 1000
                stamp = f"({total // 60:02d}m{total % 60:02d}s)"
                if s["contentType"] == "desktop":
                    n_desktop += 1
                    name = C.safe_filename(f"素材 — {s.get('classificationReason') or '桌面或应用窗口'} {stamp}.jpg")
                else:
                    n_slide += 1
                    name = C.safe_filename(f"ppt p{n_slide:03d} {stamp}.jpg")
                dst = outdir / name
                if not dst.exists() or force:
                    shutil.copy2(src, dst)
                downloaded[str(idx)] = str(dst.relative_to(work))
    finally:
        # 临时文件里是**签名直链**，无论成功、失败还是 dry-run 都必须删掉。
        # 这是硬约束：留在磁盘上就是长期凭据暴露风险。
        shutil.rmtree(tmpdir, ignore_errors=True)

    # 自检断言：临时目录必须无残留。仍存在就重试一次，再不行**直接失败**——
    # 静默留下签名直链比这次采集失败更糟。
    if tmpdir.exists():
        shutil.rmtree(tmpdir, ignore_errors=True)
    if tmpdir.exists():
        raise RuntimeError(
            f"签名直链临时目录未能清理：{tmpdir}\n"
            f"里面是平台直链（含签名），留在磁盘上属凭据暴露。请手工删除后重跑；\n"
            f"本适配器拒绝在临时目录仍有残留的情况下继续。")

    if failed:
        warnings.append(f"快照有 {len(failed)} 条下载失败（直链可能已过期，重新取一次清单）")
    return {"downloaded": downloaded, "failed": failed,
            "n_slide": n_slide, "n_desktop": n_desktop}, warnings


def write_snapshot_inventory(slides: list[dict], sdir: Path, label: str) -> Path:
    """把平台快照清单（id / 时间码 / OCR 文本 / 分类）落成 provenance 文件。

    **直链字段被剥离**——URL 不落盘。此文件不属于 manifest 契约，供人工复核。
    """
    inv = [{
        "index": s["index"],
        "imageId": s.get("_imageId"),
        "pptDocId": s.get("_pptDocId"),
        "imageSeekTime": s["startSec"],
        "imageSnapshotTime": s.get("_snapshotTime"),
        "showFlag": s.get("_showFlag"),
        "timecodeSource": s["timecodeSource"],
        "contentType": s["contentType"],
        "classificationReason": s.get("classificationReason"),
        "localFile": s.get("file"),
        "duplicateOf": s.get("duplicateOf"),
        "ocrText": s.get("ocrText"),
    } for s in slides]
    payload = C.drop_url_fields({
        "label": label,
        "note": "平台课件轨快照清单（屏幕快照轨，含教师桌面 / PowerPoint 编辑窗口）。imageUrl 为直链，未落盘。",
        "n_records": len(inv),
        "n_slide": sum(1 for r in inv if r["contentType"] == "slide"),
        "n_desktop": sum(1 for r in inv if r["contentType"] == "desktop"),
        "records": inv,
    })
    C.assert_no_secrets(payload, "native/*_snapshots.json")
    return C.write_json(sdir / "native" / f"{label}_snapshots.json", payload)


def dedupe_capture(slides: list[dict], sdir: Path, label: str, thresh: float,
                   enabled: bool, only_content_type: str = "slide") -> tuple[list[dict], str, list[str]]:
    """在**清单自带的 ``ocrText``** 上去重——**下载之前**就跑，用来减少下载量。

    为什么必须前置：快照轨的 ``ocrText`` 本来就在清单里，不需要先下载图片、也不需要跑
    EasyOCR。去重的全部意义就是**少下 20 多张冗余帧**；下载完再去重等于白下。

    复用上游 ``dedupe_slides.py``（文本 Jaccard，保留 ``lines`` 最多的那一帧），
    本函数只负责把平台 ``ocrText`` 合成它需要的 ``_ocr.json`` 形状并回填 ``duplicateOf``。
    该脚本是**纯本地文本计算**（无网络、无副作用），所以 dry-run 下也执行——
    否则 dry-run 给不出真实的下载量预估。

    返回 ``(slides, status, detail_lines)``。``status`` 是**三态之一**，
    **绝不把「未执行」写成「0 条重复」**——一个说 0 的指标会被读成「已检查且没问题」。
    """
    detail: list[str] = []
    candidates = [s for s in slides if s.get("contentType") == only_content_type]
    usable = [s for s in candidates if (s.get("ocrText") or "").strip()]

    if not enabled:
        return slides, "去重未执行（原因：--no-dedupe，动画构建帧与重复帧仍在清单里）", detail
    if len(usable) < 2:
        return slides, (f"去重未执行（原因：contentType={only_content_type} 的帧里只有 "
                        f"{len(usable)} 条带 ocrText，无法两两比较）"), detail

    # 合成 dedupe_slides.py 需要的 _ocr.json（file 用确定性占位名，只用于回填）
    synth = sdir / "native" / f"{label}_capture_ocr.json"
    payload = {
        "label": label,
        "video": None,
        "n_slides": len(usable),
        "slides": [{
            "index": s["index"],
            "file": f"snapshot_{s['index']:04d}.jpg",
            "start": s.get("startSec"),
            "end": None,
            "start_mmss": s.get("startMmss"),
            "duration": None,
            "page_guess": None,
            "text": s["ocrText"],
            "lines": [{"text": t.strip(), "conf": 1.0, "top": 0.0, "left": 0.0}
                      for t in re.split(r"[\n\r]+|(?<=[。！？；])", s["ocrText"]) if t.strip()],
        } for s in usable],
    }
    C.write_json(synth, payload)

    kept_path = sdir / "native" / f"{label}_capture_kept.json"
    C.run([C.python_exe(), C.canvas_ppt_script("dedupe_slides.py"), synth,
           "--thresh", str(thresh), "--out", str(kept_path)], check=False)
    if not kept_path.exists():
        return slides, "去重未执行（原因：dedupe_slides.py 未产出 kept 文件，请检查上游脚本）", detail

    kept = C.read_json(kept_path).get("kept", [])
    by_synth = {f"snapshot_{s['index']:04d}.jpg": s for s in usable}
    keeper_of: dict[int, int] = {}
    n_merged = 0
    for k in kept:
        keeper = by_synth.get(str(k.get("file")))
        if keeper is None:
            continue
        for gf in k.get("group_files", []):
            if str(gf) == str(k.get("file")):
                continue
            member = by_synth.get(str(gf))
            if member is None:
                continue
            keeper_of[member["index"]] = keeper["index"]
            n_merged += 1
            detail.append(f"    {member['startMmss']}（index={member['index']}）"
                          f" 与 {keeper['startMmss']}（index={keeper['index']}）合并"
                          f"（组内 {k.get('n_frames_in_group')} 帧，保留行数最多的一帧）")

    for s in slides:
        if s["index"] in keeper_of:
            s["duplicateOf"] = keeper_of[s["index"]]
            s["_dedupedOut"] = True

    n_kept = len(kept)
    if n_merged:
        status = (f"去重已执行（阈值 {thresh}）：{len(usable)} 条待比较帧 -> {n_kept} 个保留帧，"
                  f"合并掉 {n_merged} 条重复/动画构建帧（duplicateOf 非空 {n_merged} 条）")
    else:
        status = (f"去重已执行（阈值 {thresh}）：比较了 {len(usable)} 条帧，"
                  f"**未发现**相邻近重复帧（相似度均 < {thresh}）")
    return slides, status, detail


def apply_dedupe(slides: list[dict], kept_json: Path) -> tuple[list[dict], int]:
    """``dedupe_slides.py`` 的 ``group_files`` -> ``duplicateOf``（指向保留帧的 index）。"""
    kept = C.read_json(kept_json).get("kept", [])
    by_file = {Path(str(s.get("file"))).name: s["index"] for s in slides if s.get("file")}
    keep_files = {Path(str(k.get("file"))).name for k in kept}
    drop_to_keep: dict[str, int] = {}
    for k in kept:
        for gf in k.get("group_files", []):
            if str(gf) != str(k.get("file")):
                drop_to_keep[str(gf)] = by_file.get(Path(str(k.get("file"))).name)
    n = 0
    for s in slides:
        f = Path(str(s.get("file"))).name if s.get("file") else None
        if f and f not in keep_files and f in drop_to_keep:
            s["duplicateOf"] = drop_to_keep[f]
            n += 1
    return slides, n


# --------------------------------------------------------------------------
# 路径 B：从屏幕轨重建
# --------------------------------------------------------------------------

def rebuild_from_video(video: Path, work: Path, label: str, sdir: Path, args) -> list[dict]:
    """``extract_ppt_scenes.py`` -> ``ocr_slides.py`` -> ``read_slide_pageno.py`` -> ``dedupe_slides.py``。"""
    native = sdir / "native"
    native.mkdir(parents=True, exist_ok=True)
    scene_manifest = native / f"{label}.json"
    if not scene_manifest.exists() or args.force:
        C.run([C.python_exe(), C.canvas_ppt_script("extract_ppt_scenes.py"),
               video, native, label, "--thresh", str(args.thresh)], dry_run=args.dry_run)
    if args.dry_run:
        return []

    slides, _ = read_scene_manifest(scene_manifest)
    ocr_json = native / f"{label}_ocr.json"
    if not args.no_ocr and (not ocr_json.exists() or args.force):
        cmd = [C.python_exe(), C.canvas_ppt_script("ocr_slides.py"), scene_manifest]
        if args.gpu:
            cmd.append("--gpu")
        C.run(cmd, dry_run=args.dry_run)
    pageno_json = native / f"{label}_pageno.json"
    if not args.no_ocr and (not pageno_json.exists() or args.force):
        cmd = [C.python_exe(), C.canvas_ppt_script("read_slide_pageno.py"), scene_manifest]
        if args.gpu:
            cmd.append("--gpu")
        C.run(cmd, dry_run=args.dry_run)

    slides = merge_native_artifacts([scene_manifest, ocr_json, pageno_json])
    kept_json = native / f"{label}_kept.json"
    if ocr_json.exists():
        if not kept_json.exists() or args.force:
            C.run([C.python_exe(), C.canvas_ppt_script("dedupe_slides.py"), ocr_json,
                   "--thresh", str(args.dedupe_thresh)], dry_run=args.dry_run)
        if kept_json.exists():
            slides, _ = apply_dedupe(slides, kept_json)
    return slides


def read_scene_manifest(path: Path) -> tuple[list[dict], list[str]]:
    """读 ``extract_ppt_scenes.py`` 的清单。

    ⚠️ 它的 ``start_mmss`` 是 ``"%02d%02d"``（如 ``"0448"``），**缺冒号、不合 schema**；
    权威时间码取 ``start``（解码时间码，秒），``startMmss`` 一律重算。
    """
    data = C.read_json(path)
    warnings, slides = [], []
    for i, sp in enumerate(data.get("slides", []), 1):
        sec = sp.get("start")
        if sec is None:
            sec = C.parse_compact_mmss(sp.get("start_mmss"))
            warnings.append(f"{path.name} 第 {i} 条缺 start，用紧凑 start_mmss 回推（可信度较低）")
        slides.append({
            "index": i,
            "file": sp.get("file"),
            "startSec": C.to_sec(sec),
            "startMmss": C.sec_to_mmss(sec),
            "timecodeSource": "decode-scene",
            "rawTimecode": None if sec is None else str(sec),
            "source": "scene-extract",
            "contentType": "slide",
        })
    return slides, warnings


def _classify_native(obj) -> str:
    if isinstance(obj, list):
        return "ppt-capture"
    if not isinstance(obj, dict):
        return "unknown"
    if "scene_frames" in obj:
        return "legacy-extract"
    if "kept" in obj:
        return "kept"
    if isinstance(obj.get("slides"), list) and obj["slides"]:
        first = obj["slides"][0]
        if "lines" in first:
            return "ocr"
        if "page" in first and "cands" in first:
            return "pageno"
        if "start" in first:
            return "scene"
    return "unknown"


def merge_native_artifacts(paths: list[Path]) -> list[dict]:
    """按 ``file`` 归并**同一个节次内**的上游产物：场景清单给时间码，OCR 给整帧页码，页脚脚本给页脚页码。"""
    merged: dict[str, dict] = {}
    order: list[str] = []

    def slot(key: str) -> dict:
        if key not in merged:
            merged[key] = {}
            order.append(key)
        return merged[key]

    for path in paths:
        if not path or not Path(path).exists():
            continue
        data = C.read_json(path)
        kind = _classify_native(data)
        if kind == "scene":
            for sp in data.get("slides", []):
                s = slot(str(sp.get("file")))
                s.setdefault("file", str(sp.get("file")))
                s["startSec"] = C.to_sec(sp.get("start"))
                s.setdefault("timecodeSource", "decode-scene")
                s.setdefault("source", "scene-extract")
                s.setdefault("contentType", "slide")
        elif kind == "legacy-extract":
            for sp in data.get("scene_frames", []):
                s = slot(str(sp.get("file")))
                s.setdefault("file", str(sp.get("file")))
                s["startSec"] = C.parse_compact_mmss(sp.get("start_mmss"))
                s.setdefault("timecodeSource", "decode-scene")
                s["rawTimecode"] = sp.get("start_mmss")
                s.setdefault("source", "scene-extract")
                s.setdefault("contentType", "slide")
                if sp.get("page_full_ocr") is not None:
                    s["pageNoFullOcr"] = str(sp["page_full_ocr"])
        elif kind == "ocr":
            for sp in data.get("slides", []):
                s = slot(str(sp.get("file")))
                s.setdefault("file", str(sp.get("file")))
                if sp.get("page_guess") is not None:
                    s["pageNoFullOcr"] = str(sp["page_guess"])
                s["ocrText"] = sp.get("text")
                if sp.get("start") is not None:
                    s["startSec"] = C.to_sec(sp.get("start"))
                    s.setdefault("timecodeSource", "decode-scene")
                s.setdefault("source", "scene-extract")
                s.setdefault("contentType", "slide")
        elif kind == "pageno":
            for sp in data.get("slides", []):
                s = slot(str(sp.get("file")))
                s.setdefault("file", str(sp.get("file")))
                if sp.get("page") is not None:
                    s["pageNoFooterOcr"] = str(sp["page"])
                s.setdefault("source", "scene-extract")
                s.setdefault("contentType", "slide")
        elif kind == "kept":
            for sp in data.get("kept", []):
                s = slot(str(sp.get("file")))
                s.setdefault("file", str(sp.get("file")))
                s["title"] = sp.get("title") or None
                s.setdefault("source", "scene-extract")
                s.setdefault("contentType", "slide")
        elif kind == "ppt-capture":
            slides, _ = normalize_capture(data)
            for sp in slides:
                s = slot(download_ppt_filename(int(round((sp["startSec"] or 0) * 1000))))
                s.update({k: v for k, v in sp.items() if k not in ("_imgUrl", "index")})

    out = []
    for i, key in enumerate(order, 1):
        s = merged[key]
        out.append({
            "index": i,
            "file": s.get("file", key),
            "startSec": s.get("startSec"),
            "startMmss": C.sec_to_mmss(s.get("startSec")),
            "timecodeSource": s.get("timecodeSource"),
            "rawTimecode": s.get("rawTimecode"),
            "source": s.get("source", "scene-extract"),
            "contentType": s.get("contentType", "slide"),
            "pageNoFullOcr": s.get("pageNoFullOcr"),
            "pageNoFooterOcr": s.get("pageNoFooterOcr"),
            "title": s.get("title"),
            "ocrText": s.get("ocrText"),
            "duplicateOf": s.get("duplicateOf"),
        })
    return out


# --------------------------------------------------------------------------
# 转写
# --------------------------------------------------------------------------

def transcribe_session(audio: Path, sdir: Path, label: str, args) -> dict | None:
    """``transcribe_class.py``（必须分段）+ 可选 ``diarize_transcript.py`` -> transcript 契约块。"""
    native = sdir / "native"
    native.mkdir(parents=True, exist_ok=True)
    cmd = [C.python_exe(), C.canvas_ppt_script("transcribe_class.py"),
           audio, native, label, "--model", args.model, "--device", args.device,
           "--chunk", str(args.chunk)]
    if args.language:
        cmd += ["--language", args.language]
    C.run(cmd, dry_run=args.dry_run)
    plain = native / f"{label}.json"
    if args.dry_run or not plain.exists():
        return None

    chosen, diarized = plain, False
    if not args.no_diarize:
        C.run([C.python_exe(), C.canvas_ppt_script("diarize_transcript.py"), plain,
               "--wav", str(audio.resolve()), "--speakers", str(args.speakers),
               "--outdir", str(native)], dry_run=args.dry_run, check=False)
        cand = native / f"{label}（说话人）.json"
        if cand.exists():
            chosen, diarized = cand, True

    data = C.read_json(chosen)
    segs = [{
        "startSec": round(float(s.get("start", 0)), 2),
        "endSec": round(float(s.get("end", 0)), 2),
        "lang": s.get("lang") or "unknown",
        "text": (s.get("text") or "").strip(),
        "speaker": s.get("speaker") if diarized else None,
    } for s in data.get("segments", [])]
    if len(segs) > 1200 and not args.no_segments:
        args.extra_warnings.append(
            f"转写分段 {len(segs)} 条，manifest 内嵌全部会影响可读性；可用 --no-segments 只留计数")
    return {
        "file": str(chosen.relative_to(Path(args.out).resolve())),
        "model": data.get("model") or args.model,
        "chunkSec": int(args.chunk),
        "segmentCount": len(segs),
        "segments": [] if args.no_segments else segs,
        "diarized": diarized,
        "speakerLabelsAreInferred": True,
        "knownMishearings": list(args.mishearing or []),
        "qualityNote": args.transcript_note,
    }


# --------------------------------------------------------------------------
# opencli（可选，用于 --list）
# --------------------------------------------------------------------------

def opencli_eval(session: str, js: str) -> str | None:
    """调用 ``opencli browser <session> eval "<js>"``（命令形式来自 canvas-ppt SKILL.md）。

    **未在本机实测**：本环境没有已登录的 SJTU 会话，本函数只做命令拼装与失败提示。
    """
    if not C.have("opencli"):
        print("未找到 opencli。请在已登录 SJTU 的浏览器里手工执行下面的 JS，并把输出存成文件。")
        return None
    proc = C.run(["opencli", "browser", session, "eval", js], check=False, quiet=True)
    if proc is None or proc.returncode != 0:
        print(f"opencli 调用失败（rc={getattr(proc, 'returncode', '?')}）："
              f"先确认 opencli doctor 全绿、浏览器已登录 SJTU。")
        return None
    return (proc.stdout or "").strip()


def print_manual_js(js: str, hint: str) -> None:
    print(f"\n{hint}\n")
    print(js)
    print("\n（把输出保存为 JSON 文件，再用 --ppt-json / --list-json 传回来。）")


def parse_marks(pairs: list[str] | None) -> dict[int, str]:
    """解析 ``--mark-content-type 13=video`` 这类标记。"""
    out: dict[int, str] = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"--mark-content-type 需要 N=type 形式，收到：{item}")
        k, v = item.split("=", 1)
        if v not in C.CONTENT_TYPES:
            raise SystemExit(f"未知 contentType：{v}（允许：{', '.join(C.CONTENT_TYPES)}）")
        out[int(k)] = v
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="canvas.py",
        description="SJTU Canvas 课堂回放采集适配器：编排 better-awesome-sjtu-canvas-ppt 的脚本，产出 raw/。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="只读平台：不播放、不触发心跳、不点任务点。签名 URL 不落盘。",
    )
    mode = p.add_argument_group("模式")
    mode.add_argument("--list", action="store_true", help="发现节次（需要已登录 SJTU 的浏览器会话）")
    mode.add_argument("--probe-ppt", action="store_true", help="只打印探测 pptShow 的页面内 JS，不执行")

    p.add_argument("--course", help="Canvas courseId（--list 时必填）")
    p.add_argument("--course-name", default=None, help="课程名（写入 manifest.source.course.name）")
    p.add_argument("--course-code", default=None, help="课程代码，如 HLT3202G")
    p.add_argument("--tecl-id", default=None, help="教学班 id（--list 用）")
    p.add_argument("--opencli-session", default="sjtu", help="opencli 浏览器会话名（默认 sjtu）")

    p.add_argument("--session", help="节次标签，如「L08 第15节 1355」")
    p.add_argument("--out", help="工作目录（产物落 <out>/raw/）")
    p.add_argument("--platform-session-id", default=None, help="平台侧 courseId（如 179741）")
    p.add_argument("--lecture", default=None, help="课程 L 编号，如 L08。**未确认就不要填**（默认 null）")
    p.add_argument("--leti", type=int, default=None, help="节次号，如 15")
    p.add_argument("--begin-time", default=None, help="ISO 8601 上课时间")
    p.add_argument("--class-date", default=None, help="课堂日期 YYYY-MM-DD（与材料接收日期区分）")
    p.add_argument("--part-of-split", type=int, default=None, help="一节课拆多段时这是第几段（1-based）")
    p.add_argument("--duration-sec", type=float, default=None, help="该节次时长（秒）")
    p.add_argument("--used-deck", default=None,
                   help="该节实际使用的课件文件名（L08 第15节前 35 分钟用的是第9次课.pptx，未归档）")
    p.add_argument("--deck-coverage-note", default=None, help="如「0:00–35:07 未使用 S84」")

    src = p.add_argument_group("素材来源")
    src.add_argument("--ppt-json", default=None, help="路径 A：平台课件轨清单 JSON（页面拦截器导出）")
    src.add_argument("--video", default=None, help="路径 B：屏幕轨视频（本地文件，前台下载后传入）")
    src.add_argument("--audio", default=None, help="转写用 16k 单声道 wav")
    src.add_argument("--platform-subtitles", choices=["available", "unusable", "none"], default=None)
    src.add_argument("--ai-summary", default=None, help="平台 AI 概要 JSON（platformArtifacts）")
    src.add_argument("--platform-asr", default=None, help="平台逐句 ASR JSON（platformArtifacts）")
    src.add_argument("--record-variant", default=None, help="机位版本，如 record5（小文件、收音相同）")
    src.add_argument("--media-archived", action="store_true", help="媒体是否长期归档（默认否，服从知识库画像）")

    tune = p.add_argument_group("参数")
    tune.add_argument("--thresh", type=float, default=0.02, help="场景检测阈值（默认 0.02）")
    tune.add_argument("--dedupe-thresh", type=float, default=0.82,
                      help="去重文本相似度阈值（默认 0.82，对齐上游文档）")
    tune.add_argument("--download-batch", type=int, default=1,
                      help="每次调用 download_ppt.py 下载几条（默认 1，避免同秒文件名互相覆盖；0=一次性全部）")
    tune.add_argument("--chunk", type=float, default=600.0, help="转写分段秒数（**不要设 0**，整段解码会漂移）")
    tune.add_argument("--model", default="large-v3")
    tune.add_argument("--device", default="cuda")
    tune.add_argument("--language", default=None)
    tune.add_argument("--speakers", type=int, default=2)
    tune.add_argument("--gpu", action="store_true", help="OCR 用 GPU")
    tune.add_argument("--mishearing", action="append", default=None,
                      help="已知误听，可重复，如 --mishearing \"declarative→decorative\"")
    tune.add_argument("--transcript-note", default=None, help="转写质量说明（qualityNote）")
    tune.add_argument("--mark-content-type", action="append", default=None, metavar="N=TYPE",
                      help="人工标记第 N 页类型，如 13=video（L08 p13 是嵌入视频占位）")
    tune.add_argument("--footer-unit", default=None, help="该节所属单元 / 左上栏目条，如 \"KNOWLEDGE REPRESENTATION\"")

    toggle = p.add_argument_group("开关")
    toggle.add_argument("--no-ocr", action="store_true", help="路径 B 不跑 OCR/页脚读数")
    toggle.add_argument("--drop-desktop", action="store_true",
                        help="从 slides.json 剔除 contentType=desktop 的帧（默认保留并标记）")
    toggle.add_argument("--no-dedupe", action="store_true", help="跳过快照去重（**不推荐**：131 条 vs 35 页）")
    toggle.add_argument("--no-transcribe", action="store_true")
    toggle.add_argument("--no-diarize", action="store_true")
    toggle.add_argument("--no-segments", action="store_true", help="manifest 里只留分段计数，不内嵌全文")
    toggle.add_argument("--dry-run", action="store_true",
                        help="不执行子进程/下载，但仍写出 session.json 与快照清单（slides.json 等真实下载后才写）")
    toggle.add_argument("--force", action="store_true", help="重下/重算已存在的产物")
    return p


def run_path_a(args, work: Path, sdir: Path, label: str) -> tuple[list[dict], list[str], int, str, list[str]]:
    """路径 A：快照轨 -> 过滤桌面 -> **清单内去重** -> 只下载保留帧 -> raw slides。

    顺序很重要：**先过滤、再去重、最后下载**。下载是唯一昂贵的步骤，
    去重必须发生在它前面才能省下冗余帧（L08 第16节实测 63 条里 19+ 条是重复帧）。

    返回 ``(slides, warnings, recordCount, dedupe_status, dedupe_detail)``。
    """
    warnings: list[str] = []
    capture = C.read_json(Path(args.ppt_json))
    slides, warns = normalize_capture(capture)
    warnings += warns
    if not slides:
        return [], warnings, 0, "去重未执行（原因：清单里没有快照记录）", []
    n_records = len(slides)

    # 人工标记必须在去重之前（被标成 video/activity 的页不该被并进 slide 组）
    for s in slides:
        if s.get("index") in args._marks:
            s["contentType"] = args._marks[s["index"]]

    # ---- ① 过滤桌面 / 应用窗口（不下载、不登记）----
    n_desktop_all = sum(1 for s in slides if s["contentType"] == "desktop")
    if args.drop_desktop:
        before = len(slides)
        slides = [s for s in slides if s["contentType"] != "desktop"]
        if before != len(slides):
            warnings.append(f"--drop-desktop：滤掉 {before - len(slides)} 条教师桌面 / 应用窗口帧"
                            f"（不下载、不登记；如要留作素材证据就不要加这个开关）")

    # ---- ② 清单内去重（吃平台自带 ocrText，不下载、不跑 OCR）----
    slides, dedupe_status, dedupe_detail = dedupe_capture(
        slides, sdir, label, args.dedupe_thresh, enabled=not args.no_dedupe)
    warnings.append(dedupe_status)
    if not args.no_dedupe:
        warnings.append("去重在**下载之前**跑：只下载保留帧，被合并的帧共用保留帧的图片，"
                        "既省下载量也不重复归档同一页")

    # ---- ③ 只下载保留帧（duplicateOf 非空的帧跳过）----
    to_download = [s for s in slides if not s.get("duplicateOf")]
    outdir = sdir / "native" / "ppt"
    res, warns = download_track(to_download, outdir, work, args.dry_run, args.download_batch, args.force)
    warnings += warns

    # 被合并的帧共用保留帧的图片（不重复下载、不重复归档）
    keeper_by_index = {s["index"]: s for s in slides}
    for s in slides:
        if s.get("duplicateOf"):
            keeper = keeper_by_index.get(s["duplicateOf"])
            if keeper is not None:
                s["file"] = keeper.get("file")
    if not args.dry_run:
        for s in to_download:
            s["file"] = res["downloaded"].get(str(s["index"]))
        for s in slides:
            if s.get("duplicateOf"):
                keeper = keeper_by_index.get(s["duplicateOf"])
                s["file"] = keeper.get("file") if keeper else None

    write_snapshot_inventory(slides, sdir, label)
    n_dup = sum(1 for s in slides if s.get("duplicateOf"))
    warnings.append("平台快照轨与 PDF 渲染页图是两条独立证据链；跨链对齐只能由人工 "
                    "build_manifest.py --projection-map 显式给出，本适配器不猜")
    warnings.append(f"平台快照轨原始 {n_records} 条 -> 滤桌面后 {len(slides)} 条 -> "
                    f"实际下载 {len(to_download)} 张（合并帧 {n_dup} 条共用保留帧图片）")
    for s in slides:
        s.pop("_imgUrl", None)
        for k in ("_imageId", "_pptDocId", "_snapshotTime", "_showFlag", "_dedupedOut"):
            s.pop(k, None)
    return slides, warnings, n_records, dedupe_status, dedupe_detail


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.extra_warnings = []
    args._marks = parse_marks(args.mark_content_type)
    if args.chunk and args.chunk < 30:
        print("--chunk 太小（<30s）会让 Whisper 丢失上下文；建议 600。", file=sys.stderr)
        return 2

    if args.probe_ppt:
        print_manual_js(PROBE_PPT_JS.replace("<COURSE_ID>", str(args.course or "<COURSE_ID>")),
                        "在回放页控制台执行，确认 pptShow：1=有课件轨（路径 A），0=无轨（路径 B）：")
        return 0

    if args.list:
        if not args.course:
            print("--list 需要 --course", file=sys.stderr)
            return 2
        print_manual_js(INJECT_JS, "① 在回放页**立即**注入拦截器（课件轨需要它捕获 API）：")
        print_manual_js(LIST_JS.replace("<TECL_ID>", str(args.tecl_id or "<TECL_ID>")),
                        "② 列出该教学班的节次（id = 每节的 courseId，leti = 节次号，begin = 上课时间）：")
        if args.opencli_session:
            out = opencli_eval(args.opencli_session, LIST_JS.replace("<TECL_ID>", str(args.tecl_id or "")))
            if out:
                print("\nopencli 返回：\n" + out)
        print("\n提醒：一节课常拆 2 个节次，**分别采集、分别登记**；"
              "不要把平台节次号当课程 L 编号。")
        return 0

    if not args.session or not args.out:
        print("采集需要 --session 与 --out（或使用 --list / --probe-ppt）。", file=sys.stderr)
        return 2

    work = Path(args.out).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    label = args.session
    sdir = C.init_session(work, label)
    warnings: list[str] = []
    slides: list[dict] = []
    n_records = 0

    if args.ppt_json:
        slides, warnings, n_records, dedupe_status, dedupe_detail = run_path_a(
            args, work, sdir, label)
        try:
            inside = Path(args.ppt_json).expanduser().resolve().is_relative_to(work)
        except (ValueError, OSError):
            inside = False
        if inside:
            warnings.append(
                "输入的 --ppt-json 位于工作目录内：该文件含**签名直链**，不属于 raw/ 归档产物，"
                "整合前请删除或移出工作目录（不要复制进知识库）")
    elif args.video:
        video = Path(args.video).expanduser()
        if not video.is_absolute():
            video = (Path.cwd() / video).resolve()
        if not args.dry_run and not video.exists():
            print(f"找不到视频文件：{video}", file=sys.stderr)
            return 2
        slides = rebuild_from_video(video, work, label, sdir, args)
        if not args.dry_run and not slides:
            warnings.append("路径 B 未产出任何幻灯片：检查 extract_ppt_scenes.py 的 --thresh 是否过高")
        if not args.dry_run:
            warnings.append("路径 B 的时间码为解码时间码；容器时间戳可能重复，不可用 ffmpeg -ss 定位")
        # 路径 B 的抽帧没有平台 ocrText，去重要等 OCR 跑完（在 rebuild_from_video 里做）
        dedupe_status = "去重已执行（路径 B：在 ocr_slides.py 的逐页 OCR 文本上做）" \
            if not args.no_ocr else "去重未执行（原因：--no-ocr，没有文本可比）"
        dedupe_detail = []
    else:
        warnings.append("未提供 --ppt-json 或 --video：本节只有元数据，没有课件页图")
        dedupe_status, dedupe_detail = "去重未执行（原因：没有快照轨或视频输入）", []

    # 人工标记与 --drop-desktop 已在 run_path_a 里、**去重与下载之前**应用（路径 B 在此兜底）
    for s in slides:
        if s.get("index") in args._marks:
            s["contentType"] = args._marks[s["index"]]
    if args.footer_unit:
        for s in slides:
            s.setdefault("footerUnit", args.footer_unit)

    transcript = None
    if args.audio and not args.no_transcribe:
        audio = Path(args.audio).expanduser().resolve()
        if not args.dry_run and not audio.exists():
            print(f"找不到音轨：{audio}", file=sys.stderr)
            return 2
        transcript = transcribe_session(audio, sdir, label, args)
        if transcript and not args.dry_run:
            warnings.append("转写为机器转写；说话人标签为推断，引用具体观点前必须核对时间戳")
    elif not args.no_transcribe:
        warnings.append("未提供 --audio：本节没有本地转写（平台自带 ASR 只能作交叉校验）")

    if not args.dry_run:
        C.add_session_slides(work, label, slides)
    else:
        print("[dry-run] 跳过 slides.json（file 要等真实下载后才确定）；"
              "session.json / 快照清单 / warnings 仍已写出，供人工核对契约")

    artifacts = []
    for path, kind in ((args.ai_summary, "ai-summary"), (args.platform_asr, "platform-asr")):
        if path:
            artifacts.append({"kind": kind, "file": Path(path).name,
                              "note": "平台自带产物，仅作交叉校验，不直接引用"})
    if args.ppt_json:
        artifacts.append({"kind": "ppt-track-manifest", "file": Path(args.ppt_json).name,
                          "note": "平台课件轨快照清单；imageSeekTime 单位为秒",
                          "recordCount": n_records})

    media = None
    if args.video or args.audio or args.record_variant:
        video_p = Path(args.video).expanduser().resolve() if args.video else None
        media = {
            "videoPath": (video_p.name if args.media_archived and video_p else None),
            "videoSha256": None,
            "videoBytes": (video_p.stat().st_size if video_p and video_p.exists() else None),
            "audioPath": (Path(args.audio).expanduser().resolve().name if args.audio else None),
            "recordVariant": args.record_variant,
            "archived": bool(args.media_archived),
            "cleanedUpAt": None,
            "note": ("已归档" if args.media_archived
                     else "原始媒体未归档：仅作临时处理输入，处理完成后清理；需复核时从平台重新获取"),
        }

    session_meta = {
        "label": label,
        "lectureId": args.lecture,
        "platformSessionId": args.platform_session_id or args.course,
        "letiNumber": args.leti,
        "beginTime": args.begin_time,
        "classDate": args.class_date,
        "partOfSplit": args.part_of_split,
        "durationSec": args.duration_sec,
        "usedDeck": args.used_deck,
        "deckCoverageNote": args.deck_coverage_note,
        "media": media,
        "transcript": transcript,
        "platformArtifacts": artifacts,
    }
    session_meta = {k: v for k, v in session_meta.items() if v is not None or k == "label"}
    C.assert_no_secrets(session_meta, "raw/sessions/<label>/session.json")
    C.write_json(sdir / "session.json", session_meta)

    C.merge_source(work, "canvas",
                   {"id": str(args.course or "unknown"),
                    "name": args.course_name or str(args.course or "unknown"),
                    **({"code": args.course_code} if args.course_code else {}),
                    "extra": {"platformSessionId": args.platform_session_id or args.course,
                              **({"recordVariant": args.record_variant} if args.record_variant else {})}},
                   platform_subtitles=args.platform_subtitles)
    all_warnings = list(dict.fromkeys(warnings + args.extra_warnings))
    if all_warnings:
        C.add_warnings(work, all_warnings)

    C.banner("Canvas 采集完成" if not args.dry_run else "Canvas 采集 dry-run（未执行下载/转写子进程）")
    print(f"节次 sessions/{C.slugify(label)}/")
    print(f"  快照轨原始记录 {n_records} 条 -> 清单 {len(slides)} 条"
          f"（slide {sum(1 for s in slides if s.get('contentType') == 'slide')}，"
          f"desktop {sum(1 for s in slides if s.get('contentType') == 'desktop')}，"
          f"unknown {sum(1 for s in slides if s.get('contentType') == 'unknown')}，"
          f"activity {sum(1 for s in slides if s.get('contentType') == 'activity')}，"
          f"video {sum(1 for s in slides if s.get('contentType') == 'video')}）")
    # ⚠️ 去重结果**三态直说**：未执行就写未执行，绝不把「没跑」报成「0 条重复」
    print(f"  {dedupe_status}")
    if dedupe_detail:
        print("    合并样例（保留帧 <- 被合并帧）：")
        for line in dedupe_detail[:8]:
            print(line)
        if len(dedupe_detail) > 8:
            print(f"    …… 另有 {len(dedupe_detail) - 8} 条，全部写进 native/*_snapshots.json 的 duplicateOf")
    n_dup = sum(1 for s in slides if s.get("duplicateOf"))
    n_dl = sum(1 for s in slides if not s.get("duplicateOf"))
    tail = f"（其中 {n_dup} 条被合并帧共用保留帧图片）" if n_dup else ""
    print(f"  duplicateOf 非空 {n_dup} 条；实际下载 {n_dl} 张{tail}")
    print(f"  转写 {'已接入' if transcript else '无'}")
    print(f"  警告 {len(warnings) + len(args.extra_warnings)} 条 -> raw/warnings.json")
    print(f"\n下一步：python {C.SKILL_ROOT / 'scripts' / 'build_manifest.py'} \"{work}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
