#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""超星学习通采集适配器（L0 采集层）——**脱课程化**。

把有机化学库验证过的超星链路参数化，不再硬编码任何 courseid / clazzid / knowledgeid：

    课程页 mooc2-ans/mycourse/stu
      → 章节 iframe mooc2-ans/mycourse/studentcourse   （解析 toOld() 的 knowledgeid）
      → 卡片页 mooc-ans/knowledge/cards?...&knowledgeid=…&num=N
          （附件是 <iframe module="insertvideo|insertdoc" data="{JSON}">，含 objectid / jobid / mid）
      → ananas/status/<objectid>?k=&flag=normal          （时长 / download / http / pdf 直链）
      → 字幕探测 mooc-ans/richvideo/allsubtitle?mid=&objectid=&courseid=

用法
----
::

    # ② 发现章节（离线清单优先；没有清单就打印页面内 JS，不做网络请求）
    python chaoxing.py --list --course <courseid> --clazz <clazzid> [--extract-json <inventory.json>]

    # ③ 采集某一节（每个 knowledgeid 跑一次）
    python chaoxing.py --course <courseid> --clazz <clazzid> --chapter <knowledgeid> \\
        --out <work> --extract-json <inventory.json> \\
        [--video "<本地 mp4>"] [--pdf "<本地 pdf>"] [--label "L1.1 绪论"]

安全边界（与有机化学库记录一致，**不可放宽**）
---------------------------------------------
1. **只做只读 GET**；本适配器**没有任何 POST 代码路径**。
2. **绝不播放视频、绝不触发心跳 / 任务点**。每节的 ``jobid`` 只登记不使用；
   请求路径走**白名单**（见 :data:`ALLOWED_PATH_PREFIXES`），
   白名单外的路径直接拒绝——包括任何 heartbeat / log / progress 类接口。
3. **串行 + 200–300 ms 间隔**（``--min-interval-ms``，越界即报错）。
4. **cookie 只从环境变量读**（``--cookie-env``，默认 ``CHAOXING_COOKIE``），
   **不打印、不落盘、不写进任何归档文件**。
5. 超星**没有平台字幕**（``richvideo/allsubtitle`` 实测返回 ``[]``）→ 转写只能本地 Whisper。
6. 视频直链下载**必须带** ``Referer: https://mooc1.chaoxing.com/``。

被调用的上游脚本（``better-awesome-sjtu-canvas-ppt``，真实 CLI 读源码实测）::

    extract_ppt_frames.py  <video> <outdir> <label> [--interval 2.0] [--thresh 0.02]
                           [--min-dur 4.0] [--width 480]
    ocr_slides.py          <manifest> [--outdir DIR] [--gpu] [--lang ch_sim,en]
    read_slide_pageno.py   <manifest> [--gpu] [--out FILE] [--bottom 0.10] [--right 0.30]
    dedupe_slides.py       <ocr_json> [--thresh 0.80] [--out kept.json]
    transcribe_class.py    <wav> <outdir> <label> [--model large-v3] [--device cuda]
                           [--speakers 2] [--language] [--chunk 600]
    diarize_transcript.py  <transcript_json> [--wav WAV] [--speakers 2] [--backend xvector|mfcc]
                           [--stride 1] [--outdir DIR] [--teacher-label L] [--student-label L]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

REFERER = "https://mooc1.chaoxing.com/"
BASE = "https://mooc1.chaoxing.com"

# 白名单：只有这些路径前缀允许发请求。**白名单比黑名单强**——
# 任何 heartbeat / 播放上报 / 进度上报接口天然不在其中。
ALLOWED_PATH_PREFIXES = (
    "/mooc2-ans/mycourse/studentcourse",
    "/mooc2-ans/mycourse/stu",
    "/mooc-ans/knowledge/cards",
    "/ananas/status/",
    "/mooc-ans/richvideo/allsubtitle",
)

INVENTORY_JS = (
    "(()=>{const out={};"
    "document.querySelectorAll('iframe[module=insertvideo],iframe[module=insertdoc]')"
    ".forEach(f=>{try{const d=JSON.parse(f.getAttribute('data')||'{}');"
    "(out[d.objectid]=out[d.objectid]||d)}catch(e){}});"
    "return JSON.stringify(out)})()"
)


class UnsafeRequest(RuntimeError):
    """请求路径不在白名单内（可能触发学习记录）。"""


def assert_allowed(url: str) -> str:
    """校验 URL 路径在白名单内，返回 path。**任何越界路径直接拒绝。**"""
    path = urllib.parse.urlparse(url).path
    if not any(path.startswith(p) for p in ALLOWED_PATH_PREFIXES):
        raise UnsafeRequest(
            f"拒绝请求 {path}：不在只读白名单内。\n"
            f"允许的路径前缀：{', '.join(ALLOWED_PATH_PREFIXES)}\n"
            f"（播放、心跳、任务点上报类接口一律不发——超星播放会留下不可逆的完成记录。）"
        )
    return path


class Throttle:
    """串行限速器：两次请求之间至少 ``min_ms`` 毫秒（默认 250，允许 200–300）。"""

    def __init__(self, min_ms: int = 250):
        if not 200 <= min_ms <= 300:
            raise ValueError(f"--min-interval-ms 必须在 200–300 之间（给定 {min_ms}）")
        self.min_ms = min_ms
        self._last = 0.0
        self.count = 0

    def wait(self) -> None:
        now = time.monotonic()
        gap = now - self._last
        need = self.min_ms / 1000.0
        if self._last and gap < need:
            time.sleep(need - gap)
        self._last = time.monotonic()

    def tick(self) -> None:
        self.count += 1


def get_json(url: str, cookie: str, throttle: Throttle, timeout: int = 30):
    """只读 GET（白名单 + 限速 + 带 cookie 头）。**没有 POST 版本。**"""
    assert_allowed(url)
    throttle.wait()
    req = urllib.request.Request(url, headers={
        "User-Agent": C.DEFAULT_UA,
        "Referer": REFERER,
        "Cookie": cookie,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    throttle.tick()
    return body


# --------------------------------------------------------------------------
# 离线清单（浏览器导出）解析
# --------------------------------------------------------------------------

def load_inventory(path: Path) -> dict:
    """读浏览器导出的资源清单。

    兼容两种形态：有机化学的 ``{sectionNum: {num, resources:[…]}}`` 映射，
    以及扁平数组 ``[{…resource…}]``（后者统一编号为 ``1``）。
    """
    data = C.read_json(path)
    if isinstance(data, list):
        return {"1": {"num": "1", "resources": [r for r in data if isinstance(r, dict)]}}
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    raise ValueError(f"无法识别的清单格式：{path}")


def seckey(num: str):
    return [int(x) for x in re.findall(r"\d+", str(num))] or [0]


def iter_resources(inv: dict, module: str | None = None):
    """按章节号排序，yield ``(section, idx, resource)``。"""
    for sec in sorted(inv.values(), key=lambda s: seckey(s.get("num", "0"))):
        i = 0
        for r in sec.get("resources", []):
            if module and r.get("module") != module:
                continue
            i += 1
            yield sec, i, r


def find_chapter(inv: dict, knowledgeid: str) -> dict | None:
    """在清单里按 knowledgeid 找到目标节。"""
    for sec in inv.values():
        if str(sec.get("knowledgeid") or "") == str(knowledgeid):
            return sec
        for r in sec.get("resources", []):
            if str(r.get("knowledgeid") or "") == str(knowledgeid):
                return sec
    return None


# --------------------------------------------------------------------------
# 直链 -> 本地文件
# --------------------------------------------------------------------------

def download_resources(inv: dict, module: str, dest: Path, label: str,
                       dry_run: bool, force: bool, pdf_prefer: bool = False) -> tuple[list, list[str]]:
    """按清单下载某一模块的资源（前台、串行、带 Referer）。"""
    warnings: list[str] = []
    done = []
    for sec, idx, r in iter_resources(inv, module):
        name = C.safe_filename(r.get("filename") or r.get("name") or f"{module}{idx}")
        ext = (r.get("ftype") or (".pdf" if module == "insertdoc" else ".mp4")).lower()
        url = (r.get("pdfUrl") if pdf_prefer else None) or r.get("downloadUrl") or r.get("http")
        if not url:
            warnings.append(f"{sec.get('num')} 第 {idx} 条没有可下载直链（只有流地址时需要人工处理）")
            continue
        target = dest / f"{label} {name}{ext}"
        if target.exists() and not force:
            done.append(target)
            continue
        if dry_run:
            print(f"[dry-run] GET <直链已隐去> -> {target.name}")
            continue
        ok, size, msg = C.download_file(url, target, referer=REFERER)
        if ok:
            print(f"  OK  {target.name}  {(size or 0) / 1048576:.1f} MB")
            done.append(target)
        else:
            warnings.append(f"下载失败 {target.name}：{msg}（直链有实效性，过期需重新导出清单）")
    return done, warnings


# --------------------------------------------------------------------------
# 抽帧链路（复用 canvas-ppt 的关键帧脚本）
# --------------------------------------------------------------------------

def extract_slides(video: Path, sdir: Path, label: str, args) -> list[dict]:
    """``extract_ppt_frames.py`` -> OCR -> 页脚页码 -> 去重（超星无 PPT 轨，只能从视频抽）。"""
    native = sdir / "native"
    native.mkdir(parents=True, exist_ok=True)
    manifest = native / f"{label}.json"
    if not manifest.exists() or args.force:
        C.run([C.python_exe(), C.canvas_ppt_script("extract_ppt_frames.py"), video, native, label,
               "--interval", str(args.interval), "--thresh", str(args.thresh),
               "--min-dur", str(args.min_dur)], dry_run=args.dry_run)
    if args.dry_run or not manifest.exists():
        return []

    slides = []
    data = C.read_json(manifest)
    for i, sp in enumerate(data.get("slides", []), 1):
        sec = sp.get("start")
        slides.append({
            "index": i,
            "file": sp.get("file"),
            "startSec": C.to_sec(sec),
            "startMmss": C.sec_to_mmss(sec),
            "timecodeSource": "keyframe",
            "rawTimecode": None if sec is None else str(sec),
            "source": "keyframe-extract",
            "contentType": "slide",
        })

    ocr_json = native / f"{label}_ocr.json"
    if not args.no_ocr and (not ocr_json.exists() or args.force):
        cmd = [C.python_exe(), C.canvas_ppt_script("ocr_slides.py"), manifest]
        if args.gpu:
            cmd.append("--gpu")
        C.run(cmd, dry_run=args.dry_run)
    pageno_json = native / f"{label}_pageno.json"
    if not args.no_ocr and (not pageno_json.exists() or args.force):
        cmd = [C.python_exe(), C.canvas_ppt_script("read_slide_pageno.py"), manifest]
        if args.gpu:
            cmd.append("--gpu")
        C.run(cmd, dry_run=args.dry_run)

    if ocr_json.exists():
        ocr = C.read_json(ocr_json)
        by_file = {str(s.get("file")): s for s in ocr.get("slides", [])}
        for s in slides:
            o = by_file.get(str(s.get("file")))
            if o:
                s["pageNoFullOcr"] = o.get("page_guess")
                s["ocrText"] = o.get("text")
    if pageno_json.exists():
        pn = C.read_json(pageno_json)
        by_file = {str(s.get("file")): s for s in pn.get("slides", [])}
        for s in slides:
            p = by_file.get(str(s.get("file")))
            if p:
                s["pageNoFooterOcr"] = p.get("page")

    kept = native / f"{label}_kept.json"
    if ocr_json.exists():
        if not kept.exists() or args.force:
            C.run([C.python_exe(), C.canvas_ppt_script("dedupe_slides.py"), ocr_json,
                   "--thresh", str(args.dedupe_thresh)], dry_run=args.dry_run)
        if kept.exists():
            kd = C.read_json(kept)
            by_file = {Path(str(s.get("file"))).name: s["index"] for s in slides}
            keep_files = {Path(str(k.get("file"))).name for k in kd.get("kept", [])}
            for k in kd.get("kept", []):
                for gf in k.get("group_files", []):
                    if str(gf) == str(k.get("file")):
                        continue
                    target = by_file.get(Path(str(k.get("file"))).name)
                    for s in slides:
                        if Path(str(s.get("file"))).name == str(gf) and str(gf) not in keep_files:
                            s["duplicateOf"] = target
    return slides


def transcribe_session(audio: Path, sdir: Path, label: str, args) -> dict | None:
    """本地 Whisper（超星无平台字幕）。**必须分段。**"""
    native = sdir / "native"
    native.mkdir(parents=True, exist_ok=True)
    C.run([C.python_exe(), C.canvas_ppt_script("transcribe_class.py"), audio, native, label,
           "--model", args.model, "--device", args.device, "--chunk", str(args.chunk)],
          dry_run=args.dry_run)
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
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chaoxing.py",
        description="超星学习通采集适配器（只读 GET、串行限速、不播放、不触发任务点）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="courseid / clazzid / knowledgeid 全部参数化，不硬编码任何课程。",
    )
    mode = p.add_argument_group("模式")
    mode.add_argument("--list", action="store_true", help="列出章节/资源（优先读 --extract-json，离线）")
    mode.add_argument("--live", action="store_true",
                      help="走平台只读 GET（需要 --cookie-env 指向的环境变量；**未在本机实测**）")

    p.add_argument("--course", required=False, help="courseid（超星课程 id）")
    p.add_argument("--clazz", required=False, help="clazzid（教学班 id）")
    p.add_argument("--chapter", default=None, help="knowledgeid（章节/小节 id）")
    p.add_argument("--num", type=int, default=0, help="卡片序号（cards?num=N，默认 0；实测多数节为单卡片）")
    p.add_argument("--out", default=None, help="工作目录（产物落 <out>/raw/）")
    p.add_argument("--extract-json", default=None, help="浏览器导出的资源清单 JSON（离线模式）")
    p.add_argument("--cookie-env", default="CHAOXING_COOKIE",
                   help="存放 cookie 头的环境变量名（**只读环境变量，不落盘**）")
    p.add_argument("--min-interval-ms", type=int, default=250,
                   help="串行请求间隔（200–300，默认 250）")

    p.add_argument("--label", default=None, help="节次标签（默认用章节号，如「L1.1」）")
    p.add_argument("--lecture", default=None, help="课程 L 编号（**未确认就不要填**）")
    p.add_argument("--class-date", default=None, help="课堂日期 YYYY-MM-DD")
    p.add_argument("--part-of-split", type=int, default=None, help="拆段序号（1-based）")
    p.add_argument("--course-name", default=None)
    p.add_argument("--course-code", default=None)

    src = p.add_argument_group("素材来源")
    src.add_argument("--video", default=None, help="本地视频文件（前台下载后传入；抽帧用）")
    src.add_argument("--audio", default=None, help="转写用 16k 单声道 wav")
    src.add_argument("--pdf", default=None, help="该节的 PDF 课件（转交 adapters/pdf.py 处理）")
    src.add_argument("--download", action="store_true", help="按清单直链下载资源到 --dest")
    src.add_argument("--dest", default=None, help="下载目录（默认 <out>/raw/downloads/）")
    src.add_argument("--media-archived", action="store_true", help="媒体是否长期归档（默认否）")

    tune = p.add_argument_group("参数")
    tune.add_argument("--interval", type=float, default=2.0, help="抽帧采样间隔秒（extract_ppt_frames.py）")
    tune.add_argument("--thresh", type=float, default=0.02, help="抽帧差分阈值")
    tune.add_argument("--min-dur", type=float, default=4.0, help="最短课件页时长（秒）")
    tune.add_argument("--dedupe-thresh", type=float, default=0.82, help="去重文本相似度阈值")
    tune.add_argument("--chunk", type=float, default=600.0, help="转写分段秒数（**不要设 0**）")
    tune.add_argument("--model", default="large-v3")
    tune.add_argument("--device", default="cuda")
    tune.add_argument("--speakers", type=int, default=2)
    tune.add_argument("--gpu", action="store_true")
    tune.add_argument("--mishearing", action="append", default=None, help="已知误听，可重复")
    tune.add_argument("--transcript-note", default=None)
    tune.add_argument("--mark-content-type", action="append", default=None, metavar="N=TYPE",
                      help="人工标记第 N 张抽帧的类型，如 13=video")
    tune.add_argument("--footer-unit", default=None, help="该节所属单元 / 左上栏目条")
    tune.add_argument("--page-no-scope", choices=list(C.PAGE_NO_SCOPES), default=None,
                      help="页脚编号作用域：section | page | none")

    toggle = p.add_argument_group("开关")
    toggle.add_argument("--no-ocr", action="store_true")
    toggle.add_argument("--no-transcribe", action="store_true")
    toggle.add_argument("--no-diarize", action="store_true")
    toggle.add_argument("--no-segments", action="store_true")
    toggle.add_argument("--dry-run", action="store_true")
    toggle.add_argument("--force", action="store_true")
    return p


def cmd_list(args) -> int:
    if not args.extract_json:
        print("没有 --extract-json：无法离线列章节。")
        print("在已登录学习通的章节页控制台执行下面这段 JS，把输出存成清单 JSON：\n")
        print(INVENTORY_JS)
        print(f"\n或提供 --live --cookie-env {args.cookie_env}（只读 GET，串行 "
              f"{args.min_interval_ms} ms；**未在本机实测**）。")
        return 3
    inv = load_inventory(Path(args.extract_json))
    throttle = Throttle(args.min_interval_ms)
    print(f"章节 {len(inv)} 个")
    for sec, idx, r in iter_resources(inv):
        job = "有任务点" if r.get("jobid") else "无任务点"
        print(f"  {sec.get('num'):>8}  #{idx:<2} [{r.get('module')}] "
              f"{r.get('filename') or r.get('name')}  ({job}，登记不使用)")
    print("\n提醒：jobid 只登记不使用；播放会留下不可逆的完成记录。")
    print(f"节次标签请用 --label 明确指定（平台章节号不是课程 L 编号）。")
    _ = throttle
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.chunk and args.chunk < 30:
        print("--chunk 太小（<30s）会让 Whisper 丢失上下文；建议 600。", file=sys.stderr)
        return 2
    try:
        Throttle(args.min_interval_ms)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.list:
        return cmd_list(args)

    if not args.course or not args.clazz:
        print("需要 --course 与 --clazz（两者都参数化，无默认值）。", file=sys.stderr)
        return 2
    if not args.out:
        print("需要 --out。", file=sys.stderr)
        return 2

    work = Path(args.out).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    inv = load_inventory(Path(args.extract_json)) if args.extract_json else {}
    sec = find_chapter(inv, args.chapter) if (inv and args.chapter) else None
    label = args.label or (f"L{sec.get('num')}" if sec else (args.chapter or "chapter"))

    warnings: list[str] = []
    if not inv:
        warnings.append("未提供 --extract-json：无法核对 jobid / 直链，请先导出清单")
    if args.live:
        cookie = os.environ.get(args.cookie_env)
        if not cookie:
            print(f"--live 需要环境变量 {args.cookie_env} 里有 cookie 头。\n"
                  f"本适配器**不从浏览器导出 cookie、不写盘、不回显**；请学习者自行在环境变量里提供。\n"
                  f"（未提供时不做任何网络请求。）", file=sys.stderr)
            return 2
        throttle = Throttle(args.min_interval_ms)
        try:
            body = get_json(f"{BASE}/mooc-ans/knowledge/cards?clazzid={args.clazz}"
                            f"&courseid={args.course}&knowledgeid={args.chapter}&num={args.num}",
                            cookie, throttle)
            C.write_text(work / "raw" / "chaoxing" / f"cards_{args.chapter or 'root'}.html", body)
            print(f"已取 cards 页（{len(body)} 字符），串行请求 {throttle.count} 次。")
        except UnsafeRequest as exc:
            print(str(exc), file=sys.stderr)
            return 2
        warnings.append("--live 只读 GET 已实测路径白名单与限速；**平台响应解析未在本机实测**")

    sdir = C.init_session(work, label)
    slides: list[dict] = []

    if args.download:
        if not inv:
            print("--download 需要 --extract-json（直链只存在于清单里）。", file=sys.stderr)
            return 2
        dest = Path(args.dest).expanduser().resolve() if args.dest else sdir / "native" / "downloads"
        vids, w1 = download_resources(inv, "insertvideo", dest, label, args.dry_run, args.force)
        pdfs, w2 = download_resources(inv, "insertdoc", dest, label, args.dry_run, args.force, pdf_prefer=True)
        warnings += w1 + w2
        if not args.video and vids:
            args.video = str(vids[0])
        if not args.pdf and pdfs:
            args.pdf = str(pdfs[0])

    if args.video and not args.dry_run:
        v = Path(args.video).expanduser().resolve()
        if not v.exists():
            print(f"找不到视频文件：{v}", file=sys.stderr)
            return 2
        slides = extract_slides(v, sdir, label, args)
        if not slides:
            warnings.append("抽帧未产出幻灯片：检查 --interval / --thresh 与视频是否有画面")
    elif not args.video:
        warnings.append("未提供 --video：本节没有课件页图（超星无平台 PPT 轨，只能从视频抽帧）")

    # 人工标记（如某张抽帧是嵌入视频占位）
    marks: dict[int, str] = {}
    for item in args.mark_content_type or []:
        if "=" not in item:
            print(f"--mark-content-type 需要 N=type 形式，收到：{item}", file=sys.stderr)
            return 2
        k, v = item.split("=", 1)
        if v not in C.CONTENT_TYPES:
            print(f"未知 contentType：{v}（允许：{', '.join(C.CONTENT_TYPES)}）", file=sys.stderr)
            return 2
        marks[int(k)] = v
    for s in slides:
        if s.get("index") in marks:
            s["contentType"] = marks[s["index"]]
        if args.footer_unit:
            s.setdefault("footerUnit", args.footer_unit)
        if args.page_no_scope:
            s.setdefault("pageNoScope", args.page_no_scope)

    transcript = None
    if args.audio and not args.no_transcribe:
        audio = Path(args.audio).expanduser().resolve()
        if not args.dry_run and not audio.exists():
            print(f"找不到音轨：{audio}", file=sys.stderr)
            return 2
        transcript = transcribe_session(audio, sdir, label, args)
        if transcript and not args.dry_run:
            warnings.append("转写为本地 Whisper 机器转写；平台无字幕（allsubtitle 实测返回空）")
    elif not args.no_transcribe:
        warnings.append("未提供 --audio：本节没有本地转写（超星平台无可用字幕）")

    if args.pdf:
        pdf_script = C.SKILL_ROOT / "adapters" / "pdf.py"
        C.run([C.python_exe(), pdf_script, "--pdf", args.pdf, "--out", str(work),
               "--course-id", str(args.course), "--course-name", args.course_name or str(args.course)],
              dry_run=args.dry_run, check=False)
        warnings.append("该节另挂 PDF 课件：已转交 adapters/pdf.py（pdf-render 链，与抽帧链分开登记）")

    if not args.dry_run:
        C.add_session_slides(work, label, slides)
        media = None
        if args.video or args.audio:
            vp = Path(args.video).expanduser().resolve() if args.video else None
            media = {
                "videoPath": (vp.name if args.media_archived and vp else None),
                "videoSha256": None,
                "videoBytes": (vp.stat().st_size if vp and vp.exists() else None),
                "audioPath": (Path(args.audio).expanduser().resolve().name if args.audio else None),
                "recordVariant": None,
                "archived": bool(args.media_archived),
                "cleanedUpAt": None,
                "note": ("已归档" if args.media_archived
                         else "原始媒体未归档：仅作临时处理输入，处理完成后清理"),
            }
        session_meta = {
            "label": label,
            "lectureId": args.lecture,
            "platformSessionId": args.chapter,
            "letiNumber": None,
            "beginTime": None,
            "classDate": args.class_date,
            "partOfSplit": args.part_of_split,
            "media": media,
            "transcript": transcript,
            "platformArtifacts": [],
        }
        session_meta = {k: v for k, v in session_meta.items() if v is not None or k == "label"}
        C.assert_no_secrets(session_meta, "raw/sessions/<label>/session.json")
        C.write_json(sdir / "session.json", session_meta)

        C.merge_source(work, "chaoxing",
                       {"id": str(args.course),
                        "name": args.course_name or str(args.course),
                        **({"code": args.course_code} if args.course_code else {}),
                        "extra": {"clazzid": str(args.clazz), "knowledgeid": args.chapter}},
                       platform_subtitles="none")
        all_warnings = list(dict.fromkeys(warnings + (args.extra_warnings if hasattr(args, "extra_warnings") else [])))
        if all_warnings:
            C.add_warnings(work, all_warnings)

    C.banner("超星采集完成" if not args.dry_run else "超星采集 dry-run（未执行子进程）")
    print(f"节次 sessions/{C.slugify(label)}/  幻灯片 {len(slides)} 张")
    print(f"只读纪律：串行 GET、间隔 {args.min_interval_ms} ms、不播放、不触发心跳。")
    print(f"\n下一步：python {C.SKILL_ROOT / 'scripts' / 'build_manifest.py'} \"{work}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
