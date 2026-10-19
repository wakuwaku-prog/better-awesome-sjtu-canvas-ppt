#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_manifest.py — 只读校验 manifest.json（素材清单契约 v1，最终版）。

设计原则
--------
校验「**状态声明与读数是否自洽**」，不是「凡有异常就报错」。契约里有三类
**合法**的「看起来不对」：

* `pageNoStatus: "anomalous"` —— L08 p24 页脚印 42、前后页 08/09。
  两路一致但偏离单元序列，按原样保留，**要求 pageNoNote 非空，但不报错**。
* `pageNoScope: "per-unit"` 下的**跨单元编号重复** —— L08 页脚 03 出现在
  p3/p8/p9/p11，02 出现在 p2/p7/p22……这是**正常**的。只有同一 `footerUnit`
  内重复才是 `duplicate-in-unit`。
* `projections: []` —— 合法且有意义（= 未见对应讲解），只汇总打印，不报错。

时间码单调性：`startSec` 现在挂在 `projections[]` 上，所以**先按 sessionIndex
分组**再查组内单调递增；组内允许 `isReplay: true` 的记录回退（L08 p29 在
41:12 放映、42:49 重放）。

用法
----
python check_manifest.py <manifest.json> [--schema <manifest.schema.json>] [--quiet] [--json]

退出码
------
0 = 无 ERROR；1 = 有 ERROR 或读不了；2 = 用法错误

本脚本**只读**：不写、不改、不创建任何文件。
"""
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

MMSS_RE = re.compile(r"^([0-9]{1,2}):([0-9]{2})$")
TIME_TOLERANCE_SEC = 1.0

PAGE_STATUS = ("verified", "anomalous", "mismatch", "missing", "not-applicable")
PAGE_SCOPE = ("section", "page", "none")
CONTENT_TYPE = ("slide", "video", "activity", "desktop", "unknown")
TIMECODE_SOURCE = ("platform-imageSeekTime-sec", "platform-createSec-ms",
                   "decode-scene", "keyframe", "manual")
SLIDE_SOURCE = ("platform-ppt-track", "scene-extract", "keyframe-extract",
                "pdf-render")
# 这些字段在最终版里已从 slides[] 移到 projections[] / 改名
LEGACY_SLIDE_FIELDS = {
    "startSec": "已移入 slides[].projections[].startSec",
    "startMmss": "已移入 slides[].projections[].startMmss",
    "sessionIndex": "已移入 slides[].projections[].sessionIndex",
    "timecodeSource": "已移入 slides[].projections[].timecodeSource",
    "rawTimecode": "已移入 slides[].projections[].rawTimecode",
    "isSlide": "已由 slides[].contentType 取代",
}


class Report(object):
    def __init__(self):
        self.findings = []
        self.checked = 0

    def add(self, level, rule, message):
        self.findings.append((level, rule, message))

    def error(self, rule, message):
        self.add("ERROR", rule, message)

    def warn(self, rule, message):
        self.add("WARN", rule, message)

    def info(self, rule, message):
        self.add("INFO", rule, message)

    def count(self, level):
        return sum(1 for f in self.findings if f[0] == level)

    @property
    def ok(self):
        return self.count("ERROR") == 0

    def render(self, title):
        lines = ["=" * 72, "check_manifest.py — %s" % title, "=" * 72,
                 "检查项 %d 条；ERROR %d，WARN %d，INFO %d"
                 % (self.checked, self.count("ERROR"),
                    self.count("WARN"), self.count("INFO")), ""]
        for level in ("ERROR", "WARN", "INFO"):
            group = [f for f in self.findings if f[0] == level]
            if not group:
                continue
            lines.append("--- %s (%d) ---" % (level, len(group)))
            for _, rule, message in group:
                lines.append("  [%s] %s" % (rule, message))
            lines.append("")
        lines.append("RESULT: %s" % ("PASS（无 ERROR）" if self.ok
                                     else "FAIL（%d 个 ERROR）" % self.count("ERROR")))
        return "\n".join(lines)


# ---------------------------------------------------------------- 工具

def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def is_str(v):
    return isinstance(v, str)


def nonempty(v):
    return isinstance(v, str) and v.strip() != ""


def mmss_to_sec(s):
    m = MMSS_RE.match(s or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def normalize_page_no(raw):
    """把读数归一化成纯数字字符串。

    pageNoFullOcr 可能含版式标注（如「实验心理学 11」），比较必须用归一化值。
    优先用显式 pageNoNormalized；没有就从读数里抽最后一串数字。
    """
    if raw is None:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        return str(raw)
    if not isinstance(raw, str):
        return None
    nums = re.findall(r"\d+", raw)
    if not nums:
        return None
    return nums[-1]


def get(d, key, default=None):
    return d.get(key, default) if isinstance(d, dict) else default


# ---------------------------------------------------------------- 顶层

def check_top(man, rep):
    for key in ("schemaVersion", "source", "slides", "warnings"):
        rep.checked += 1
        if key not in man:
            rep.error("manifest.required", "顶层缺必填字段 %r" % key)
    rep.checked += 1
    if man.get("schemaVersion") != 1:
        rep.error("manifest.schemaVersion",
                  "schemaVersion 必须是数字 1，实际 %r" % man.get("schemaVersion"))
    for key in ("media", "transcript"):
        if key in man:
            rep.error("manifest.legacyTopLevel",
                      "顶层出现 v0 字段 %r —— 最终版已把它移入 source.sessions[]" % key)
    src = get(man, "source", {})
    if isinstance(src, dict) and "session" in src:
        rep.error("manifest.legacySession",
                  "source.session 是 v0 结构，最终版是 source.sessions[]")

    rep.checked += 1
    sessions = get(src, "sessions", None)
    has_sessions = isinstance(sessions, list) and len(sessions) > 0
    if not has_sessions and man.get("pdf") is None:
        rep.error("manifest.sourceOrPdf",
                  "source.sessions 为空且没有 pdf —— 二者不能同时为空")


# ---------------------------------------------------------------- source

def check_source(man, rep):
    src = get(man, "source")
    if not isinstance(src, dict):
        rep.error("source.type", "source 必须是对象")
        return []
    rep.checked += 1
    if src.get("platform") not in ("canvas", "chaoxing", "pdf", "unknown"):
        rep.error("source.platform", "platform 取值非法：%r" % src.get("platform"))
    course = get(src, "course")
    if not isinstance(course, dict):
        rep.error("source.course", "source.course 必须是对象")
    else:
        for key in ("id", "name"):
            rep.checked += 1
            if not nonempty(course.get(key)):
                rep.error("source.course.%s" % key,
                          "course.%s 必须是非空字符串（取不到写 \"unknown\"，不要编）" % key)
    rep.checked += 1
    if not nonempty(src.get("capturedAt")):
        rep.error("source.capturedAt", "capturedAt 必须是非空字符串（ISO 8601）")
    if "platformSubtitles" in src and src["platformSubtitles"] not in (
            "available", "unusable", "none"):
        rep.error("source.platformSubtitles",
                  "platformSubtitles 取值非法：%r" % src["platformSubtitles"])

    sessions = src.get("sessions")
    if sessions is None:
        rep.error("source.sessions", "source.sessions 是必填字段")
        return []
    if not isinstance(sessions, list):
        rep.error("source.sessions", "source.sessions 必须是数组")
        return []

    rep.info("source.sessions", "sessions 共 %d 个" % len(sessions))
    seen_split = {}
    for i, s in enumerate(sessions):
        at = "sessions[%d]" % i
        rep.checked += 1
        if not isinstance(s, dict):
            rep.error("session.type", "%s 必须是对象" % at)
            continue
        rep.checked += 1
        if not nonempty(s.get("label")):
            rep.error("session.label", "%s.label 必须非空" % at)
        if "durationSec" in s and s["durationSec"] is not None \
                and not is_num(s["durationSec"]):
            rep.error("session.durationSec", "%s.durationSec 必须是数字" % at)
        if s.get("usedDeck") is not None and not is_str(s["usedDeck"]):
            rep.error("session.usedDeck", "%s.usedDeck 必须是字符串或 null" % at)

        lid, part = s.get("lectureId"), s.get("partOfSplit")
        if lid is not None and part is not None:
            rep.checked += 1
            key = (lid, part)
            if key in seen_split:
                rep.error("session.partOfSplit",
                          "%s: lectureId=%s 下 partOfSplit=%r 与 sessions[%d] 重复"
                          % (at, lid, part, seen_split[key]))
            else:
                seen_split[key] = i

        check_session_media(s, at, rep)
        check_session_transcript(s, at, rep)
        check_session_artifacts(s, at, rep)

    for i, s in enumerate(sessions):
        if not isinstance(s, dict):
            continue
        at = "sessions[%d]" % i
        has_media = isinstance(s.get("media"), dict)
        has_tr = isinstance(s.get("transcript"), dict)
        rep.checked += 1
        if (has_media or has_tr) and s.get("durationSec") is None:
            rep.warn("session.durationSelfConsistency",
                     "%s 有 media/transcript 但 durationSec 为 null"
                     "——无法核对时间码上界" % at)
        if s.get("durationSec") is not None and not has_media and not has_tr:
            rep.warn("session.durationSelfConsistency",
                     "%s 声明 durationSec 但既无 media 也无 transcript" % at)
    return sessions


def check_session_media(s, at, rep):
    media = s.get("media")
    if media is None:
        return
    if not isinstance(media, dict):
        rep.error("session.media", "%s.media 必须是对象或 null" % at)
        return
    rep.checked += 1
    archived = media.get("archived")
    if not isinstance(archived, bool):
        rep.error("media.archived", "%s.media.archived 必须是布尔值" % at)
        return
    if archived:
        rep.checked += 1
        if not (nonempty(media.get("videoPath")) or nonempty(media.get("audioPath"))):
            rep.error("media.archivedWithoutPath",
                      "%s.media.archived=true 但没有 videoPath/audioPath" % at)


def check_session_transcript(s, at, rep):
    tr = s.get("transcript")
    if tr is None:
        return
    if not isinstance(tr, dict):
        rep.error("session.transcript", "%s.transcript 必须是对象或 null" % at)
        return
    segs, declared = tr.get("segments"), tr.get("segmentCount")
    if segs is not None:
        rep.checked += 1
        if not isinstance(segs, list):
            rep.error("transcript.segments", "%s.transcript.segments 必须是数组" % at)
        else:
            if declared is not None and segs:
                # segmentCount 也可以指**外部转写文件**的段数（fixture 实测
                # segmentCount=533 而 segments 未内联）。只有内联了 segments
                # 才做长度比对，否则会把「没内联」误判成不一致。
                rep.checked += 1
                if declared != len(segs):
                    rep.error("transcript.segmentCount",
                              "%s.transcript.segmentCount=%r 与内联 segments 长度 %d 不一致"
                              % (at, declared, len(segs)))
            for j, seg in enumerate(segs):
                rep.checked += 1
                if not isinstance(seg, dict):
                    rep.error("transcript.segment",
                              "%s.transcript.segments[%d] 必须是对象" % (at, j))
                    continue
                st, en = seg.get("startSec"), seg.get("endSec")
                if not (is_num(st) and is_num(en)):
                    rep.error("transcript.segment.time",
                              "%s.transcript.segments[%d] 缺 startSec/endSec" % (at, j))
                elif en < st:
                    rep.error("transcript.segment.order",
                              "%s.transcript.segments[%d] endSec(%s) < startSec(%s)"
                              % (at, j, en, st))
                if not nonempty(seg.get("text")):
                    rep.error("transcript.segment.text",
                              "%s.transcript.segments[%d].text 为空" % (at, j))
    rep.checked += 1
    if tr.get("speakerLabelsAreInferred") is not True:
        rep.error("transcript.speakerLabelsAreInferred",
                  "%s.transcript.speakerLabelsAreInferred 必须为 true"
                  "（自动说话人标签恒为推断），实际 %r"
                  % (at, tr.get("speakerLabelsAreInferred")))


def check_session_artifacts(s, at, rep):
    arts = s.get("platformArtifacts")
    if arts is None:
        return
    if not isinstance(arts, list):
        rep.error("session.platformArtifacts", "%s.platformArtifacts 必须是数组" % at)
        return
    for j, a in enumerate(arts):
        rep.checked += 1
        if not isinstance(a, dict):
            rep.error("platformArtifact.type",
                      "%s.platformArtifacts[%d] 必须是对象" % (at, j))
            continue
        if a.get("kind") not in ("ai-summary", "platform-asr",
                                 "ppt-track-manifest", "other"):
            rep.error("platformArtifact.kind",
                      "%s.platformArtifacts[%d].kind 非法：%r" % (at, j, a.get("kind")))
        if not nonempty(a.get("file")):
            rep.error("platformArtifact.file",
                      "%s.platformArtifacts[%d].file 必须非空" % (at, j))
        if "recordCount" in a and a["recordCount"] is not None \
                and not is_int(a["recordCount"]):
            rep.error("platformArtifact.recordCount",
                      "%s.platformArtifacts[%d].recordCount 必须是整数" % (at, j))


# ---------------------------------------------------------------- slides

def check_slides(man, rep, sessions):
    slides = man.get("slides")
    if not isinstance(slides, list):
        rep.error("slides.type", "slides 必须是数组")
        return []
    rep.info("slides", "slides 共 %d 页" % len(slides))

    for pos, sl in enumerate(slides):
        rep.checked += 1
        if not isinstance(sl, dict):
            rep.error("slide.type", "slides[%d] 必须是对象" % pos)
            continue
        idx = sl.get("index")
        if not is_int(idx):
            rep.error("slide.index", "slides[%d].index 必须是整数" % pos)
        elif idx != pos + 1:
            rep.error("slide.indexContinuous",
                      "slides[%d].index=%r，应为 %d（必须从 1 连续）" % (pos, idx, pos + 1))
        if not nonempty(sl.get("file")):
            rep.error("slide.file", "slides[%d].file 必须非空" % pos)
        if sl.get("source") not in SLIDE_SOURCE:
            rep.error("slide.source",
                      "slides[%d].source 非法：%r" % (pos, sl.get("source")))
        for legacy, why in LEGACY_SLIDE_FIELDS.items():
            if legacy in sl:
                rep.error("slide.legacyField",
                          "slides[%s] 出现已作废字段 %r（%s）"
                          % (sl.get("index"), legacy, why))

    n_sessions = len(sessions) if isinstance(sessions, list) else 0
    for sl in slides:
        if not isinstance(sl, dict):
            continue
        at = "slides[%s]" % sl.get("index")
        check_slide_content_type(sl, at, rep)
        check_slide_projections(sl, at, n_sessions, rep)
        check_slide_page_no(sl, at, rep)

    check_projection_monotonic(slides, rep)
    check_page_no_units(slides, rep)
    summarize_unseen(slides, rep)
    return slides


def check_slide_content_type(sl, at, rep):
    ct = sl.get("contentType")
    if ct is not None:
        rep.checked += 1
        if ct not in CONTENT_TYPE:
            rep.error("slide.contentType", "%s.contentType 非法：%r" % (at, ct))
    # 路径 A 的关键断言：platform-ppt-track 是屏幕快照轨，采集层必须已过滤桌面
    if sl.get("source") == "platform-ppt-track":
        rep.checked += 1
        if ct == "desktop":
            rep.error("slide.desktopNotFiltered",
                      "%s 来源 platform-ppt-track 但 contentType=desktop"
                      "——采集层必须已过滤教师桌面/应用窗口截图" % at)
        elif ct in ("slide", "video", "activity"):
            pass
        elif ct is None:
            rep.warn("slide.contentTypeMissing",
                     "%s 来源 platform-ppt-track 但未标 contentType" % at)
        elif ct == "unknown":
            rep.warn("slide.contentTypeUnknown",
                     "%s contentType=unknown —— 屏幕快照轨需人工判过" % at)


def check_slide_projections(sl, at, n_sessions, rep):
    projs = sl.get("projections")
    if projs is None:
        rep.warn("slide.projectionsMissing",
                 "%s 没有 projections 字段（空数组才表示「未见对应讲解」）" % at)
        return
    rep.checked += 1
    if not isinstance(projs, list):
        rep.error("slide.projections", "%s.projections 必须是数组" % at)
        return
    for k, p in enumerate(projs):
        pat = "%s.projections[%d]" % (at, k)
        rep.checked += 1
        if not isinstance(p, dict):
            rep.error("projection.type", "%s 必须是对象" % pat)
            continue
        si = p.get("sessionIndex")
        rep.checked += 1
        if not is_int(si):
            rep.error("projection.sessionIndex",
                      "%s.sessionIndex 必填且必须是整数，实际 %r" % (pat, si))
        elif n_sessions == 0:
            rep.error("projection.sessionIndex",
                      "%s.sessionIndex=%d 但 source.sessions 为空" % (pat, si))
        elif not (1 <= si <= n_sessions):
            rep.error("projection.sessionIndexRange",
                      "%s.sessionIndex=%d 超出 sessions 范围 1..%d"
                      % (pat, si, n_sessions))

        start = p.get("startSec")
        rep.checked += 1
        if not is_num(start):
            rep.error("projection.startSec",
                      "%s.startSec 必填且必须是数字，实际 %r" % (pat, start))
        mmss = p.get("startMmss")
        if mmss is not None:
            rep.checked += 1
            sec = mmss_to_sec(mmss)
            if sec is None:
                rep.error("projection.startMmssFormat",
                          "%s.startMmss=%r 不符合 M:SS / MM:SS" % (pat, mmss))
            elif is_num(start) and abs(sec - start) > TIME_TOLERANCE_SEC:
                rep.error("projection.startMmssMismatch",
                          "%s.startMmss=%r (=%d 秒) 与 startSec=%s 不一致（容差 %.0f 秒）"
                          % (pat, mmss, sec, start, TIME_TOLERANCE_SEC))
        else:
            rep.checked += 1
            if is_num(start) and "startMmss" not in p:
                rep.warn("projection.startMmssMissing",
                         "%s 有 startSec=%s 但没写 startMmss（建议补上，便于人工核对）"
                         % (pat, start))

        tcs = p.get("timecodeSource")
        rep.checked += 1
        if tcs is None:
            rep.error("projection.timecodeSource",
                      "%s.timecodeSource 必填（防止单位混淆）" % pat)
        elif tcs not in TIMECODE_SOURCE:
            rep.error("projection.timecodeSource",
                      "%s.timecodeSource 非法：%r" % (pat, tcs))

        # 单位口径自洽
        raw = p.get("rawTimecode")
        if nonempty(raw) and is_num(start) and tcs in (
                "platform-imageSeekTime-sec", "platform-createSec-ms"):
            rep.checked += 1
            try:
                raw_val = float(raw)
            except ValueError:
                raw_val = None
            if raw_val is not None:
                expect = raw_val / 1000.0 if tcs == "platform-createSec-ms" else raw_val
                if abs(expect - start) > 1.0:
                    rep.warn("projection.timecodeUnit",
                             "%s.timecodeSource=%s，rawTimecode=%r → %.3f 秒，"
                             "与 startSec=%s 不符"
                             % (pat, tcs, raw, expect, start))

        if p.get("isReplay") is not None and not isinstance(p["isReplay"], bool):
            rep.error("projection.isReplay", "%s.isReplay 必须是布尔值" % pat)
        en = p.get("endSec")
        if en is not None:
            rep.checked += 1
            if not is_num(en):
                rep.error("projection.endSec", "%s.endSec 必须是数字" % pat)
            elif is_num(start) and en < start:
                rep.error("projection.endSecOrder",
                          "%s.endSec(%s) < startSec(%s)" % (pat, en, start))


def check_projection_monotonic(slides, rep):
    """按 sessionIndex 分组后检查组内 startSec 单调递增。

    同一 session 内允许 isReplay=true 的记录回退（L08 p29 41:12 放映、42:49 重放）。
    """
    by_session = {}
    for sl in slides:
        if not isinstance(sl, dict):
            continue
        for p in (sl.get("projections") or []):
            if not isinstance(p, dict):
                continue
            si = p.get("sessionIndex")
            if not is_int(si) or not is_num(p.get("startSec")):
                continue
            by_session.setdefault(si, []).append(
                (p["startSec"], sl.get("index"), bool(p.get("isReplay"))))

    for si, records in sorted(by_session.items()):
        records.sort(key=lambda r: (r[0], r[1] if r[1] is not None else 0))
        rep.checked += 1
        prev = None
        prev_idx = None
        for start, idx, is_replay in records:
            if prev is not None and start < prev:
                if is_replay:
                    rep.info("projection.replayRegression",
                             "session %d：slides[%s] 在 %s 秒重放（上一记录 %s 秒）"
                             "——isReplay=true，允许回退" % (si, idx, start, prev))
                else:
                    rep.error("projection.monotonic",
                              "session %d 内时间码回退：slides[%s] startSec=%s "
                              "小于前一条 slides[%s] 的 %s，且未标 isReplay=true"
                              % (si, idx, start, prev_idx, prev))
            prev, prev_idx = start, idx
        rep.info("projection.monotonic",
                 "session %d：%d 条放映记录，组内单调性已查" % (si, len(records)))
    if not by_session:
        rep.info("projection.monotonic", "没有任何放映记录（全部 projections 为空）")


def check_slide_page_no(sl, at, rep):
    status = sl.get("pageNoStatus")
    if status is None:
        return
    rep.checked += 1
    if status not in PAGE_STATUS:
        rep.error("slide.pageNoStatus", "%s.pageNoStatus 非法：%r" % (at, status))
        return

    scope = sl.get("pageNoScope")
    if scope is not None:
        rep.checked += 1
        if scope not in PAGE_SCOPE:
            rep.error("slide.pageNoScope", "%s.pageNoScope 非法：%r" % (at, scope))

    full = sl.get("pageNoFullOcr")
    foot = sl.get("pageNoFooterOcr")
    expect = sl.get("pageNoExpected")
    norm = sl.get("pageNoNormalized")
    note = sl.get("pageNoNote")

    nf, no = normalize_page_no(full), normalize_page_no(foot)
    ne = normalize_page_no(expect)

    # 归一化字段与读数自洽
    if norm is not None:
        rep.checked += 1
        if normalize_page_no(norm) != norm:
            rep.warn("slide.pageNoNormalized",
                     "%s.pageNoNormalized=%r 不是纯数字" % (at, norm))
        if nf is not None and norm != nf:
            rep.warn("slide.pageNoNormalized",
                     "%s.pageNoNormalized=%r 与 pageNoFullOcr=%r 的归一化值 %r 不符"
                     % (at, norm, full, nf))

    if status == "verified":
        # verified 只要求两路读数一致（归一化后）。
        # 不比对 pageNoExpected：页脚是小节/版式编号，pageNoExpected 由采集层
        # 从读数归纳而来，拿它当「应印值」会自我循环（L08 p24 就是 exp==读数）。
        rep.checked += 1
        if nf is None or no is None:
            rep.error("slide.pageNoVerified",
                      "%s.pageNoStatus=verified 但两路读数不全（full=%r, footer=%r）"
                      % (at, full, foot))
        elif nf != no:
            rep.error("slide.pageNoVerified",
                      "%s.pageNoStatus=verified 但两路读数不一致（full=%r, footer=%r）"
                      "——不一致应标 mismatch" % (at, full, foot))

    elif status == "anomalous":
        # 合法状态：两路一致，但明显不符合所属小节序列（L08 p24 页脚印 42）。
        # 判断依据写在 pageNoNote 里；本校验只查「声明与读数自洽」，不报错。
        rep.checked += 1
        if nf is None or no is None:
            rep.error("slide.pageNoAnomalous",
                      "%s.pageNoStatus=anomalous 但两路读数不全（full=%r, footer=%r）"
                      % (at, full, foot))
        elif nf != no:
            rep.error("slide.pageNoAnomalous",
                      "%s.pageNoStatus=anomalous 要求两路一致，实际 full=%r footer=%r"
                      % (at, full, foot))
        rep.checked += 1
        if not nonempty(note):
            rep.error("slide.pageNoAnomalousNote",
                      "%s.pageNoStatus=anomalous 必须写 pageNoNote"
                      "（写明原始读数与判断依据）" % at)

    elif status == "mismatch":
        rep.checked += 1
        if nf is None or no is None:
            rep.error("slide.pageNoMismatch",
                      "%s.pageNoStatus=mismatch 但两路读数不全（full=%r, footer=%r）"
                      % (at, full, foot))
        elif nf == no:
            rep.error("slide.pageNoMismatch",
                      "%s.pageNoStatus=mismatch 但两路读数相同（都是 %r）"
                      "——不一致才叫 mismatch" % (at, nf))
        rep.checked += 1
        if not nonempty(note):
            rep.warn("slide.pageNoMismatchNote",
                     "%s.pageNoStatus=mismatch 建议写 pageNoNote 说明原始读数" % at)

    elif status == "missing":
        rep.checked += 1
        if nf is not None or no is not None:
            rep.warn("slide.pageNoMissing",
                     "%s.pageNoStatus=missing 但仍有读数（full=%r, footer=%r）"
                     % (at, full, foot))
        rep.checked += 1
        if not nonempty(note):
            rep.error("slide.pageNoMissingNote",
                      "%s.pageNoStatus=missing 必须写 pageNoNote"
                      "（说明是无页脚还是底部为其他内容行）" % at)

    elif status == "not-applicable":
        rep.checked += 1
        if nf is not None or no is not None:
            rep.warn("slide.pageNoNotApplicable",
                     "%s.pageNoStatus=not-applicable 但读到了读数（full=%r, footer=%r）"
                     % (at, full, foot))


def check_page_no_units(slides, rep):
    """页脚**不是页码，是小节/版式编号**——重复是正常现象，只汇总打印。

    P08 对照表实测：p26–p31 与 p33 页脚**都是 10**，p8/p9/p11 都是 03，
    p17/p18 都是 06。所以「页脚数值与其他页相同」**不报错、不警告**。
    这里只统计并打印，方便人工核对，不产生任何 finding。
    """
    by_no = {}
    for sl in slides:
        if not isinstance(sl, dict):
            continue
        no = normalize_page_no(sl.get("pageNoFullOcr")) \
            or normalize_page_no(sl.get("pageNoFooterOcr"))
        if no is None:
            continue
        by_no.setdefault(no, []).append(sl.get("index"))

    repeated = {no: idxs for no, idxs in by_no.items() if len(idxs) > 1}
    if repeated:
        rep.info("slide.footerRepeat",
                 "页脚编号重复 %d 组（section 作用域下正常，不报错）：%s"
                 % (len(repeated),
                    "; ".join("编号 %s → slides %s" % (n, v)
                              for n, v in sorted(repeated.items())[:8])))
    else:
        rep.info("slide.footerRepeat", "没有页脚编号重复")


def summarize_unseen(slides, rep):
    unseen = [sl.get("index") for sl in slides
              if isinstance(sl, dict) and not (sl.get("projections") or [])]
    if unseen:
        rep.info("slide.unseen",
                 "有 %d 页未见对应讲解（projections 为空，合法）：%s"
                 % (len(unseen), unseen[:20]))
    else:
        rep.info("slide.unseen", "所有页都有放映记录")


# ---------------------------------------------------------------- 其余块

def check_pdf(man, rep):
    pdf = man.get("pdf")
    if pdf is None:
        return
    if not isinstance(pdf, dict):
        rep.error("pdf.type", "pdf 必须是对象或 null")
        return
    if pdf.get("pageCount") is not None and not is_int(pdf["pageCount"]):
        rep.error("pdf.pageCount", "pdf.pageCount 必须是整数")
    if pdf.get("isSelection") is True:
        # 选页必须留痕，否则复核会以为课件完整
        rep.checked += 1
        has_note = nonempty(pdf.get("note")) or bool(man.get("warnings")) \
            or bool(man.get("unarchivedMaterials"))
        if not has_note:
            rep.error("pdf.selectionNotDocumented",
                      "pdf.isSelection=true 但 warnings / unarchivedMaterials / "
                      "pdf.note 里都没有对应说明——选页必须留痕")


def check_register(man, rep):
    entries = man.get("sourceRegisterEntries")
    if entries is None:
        return
    if not isinstance(entries, list):
        rep.error("sourceRegisterEntries.type", "sourceRegisterEntries 必须是数组")
        return
    for i, e in enumerate(entries):
        rep.checked += 1
        if not isinstance(e, dict):
            rep.error("sourceRegisterEntry.type",
                      "sourceRegisterEntries[%d] 必须是对象" % i)
            continue
        for key in ("type", "source"):
            rep.checked += 1
            if not nonempty(e.get(key)):
                rep.error("sourceRegisterEntry.%s" % key,
                          "sourceRegisterEntries[%d].%s 必须非空" % (i, key))


def check_unarchived(man, rep):
    mats = man.get("unarchivedMaterials")
    if mats is None:
        return
    if not isinstance(mats, list):
        rep.error("unarchivedMaterials.type", "unarchivedMaterials 必须是数组")
        return
    rep.info("unarchivedMaterials", "未归档材料 %d 条" % len(mats))
    for i, m in enumerate(mats):
        rep.checked += 1
        if not isinstance(m, dict):
            rep.error("unarchivedMaterial.type",
                      "unarchivedMaterials[%d] 必须是对象" % i)
            continue
        for key in ("what", "evidence"):
            rep.checked += 1
            if not nonempty(m.get(key)):
                rep.error("unarchivedMaterial.%s" % key,
                          "unarchivedMaterials[%d].%s 必须非空"
                          "（未归档材料必须留痕，否则复核会以为课件完整）" % (i, key))


def check_used_deck(man, rep, sessions):
    """session.usedDeck 非空时，应在 unarchivedMaterials 或 Source Register 里留痕。

    意图是防「课堂实际用了另一份课件却没人记」——L08 前 35 分钟用的是
    `第9次课.pptx`（未归档）。已归档/已登记的课件（如 S84 的 `第8次课.pdf`）
    在 sourceRegisterEntries 里有行，同样算留痕。
    """
    mats = man.get("unarchivedMaterials") or []
    regs = man.get("sourceRegisterEntries") or []
    blob_parts = []
    for m in mats:
        if isinstance(m, dict):
            blob_parts += [str(m.get("what", "")), str(m.get("evidence", "")),
                           str(m.get("impact") or "")]
    for r in regs:
        if isinstance(r, dict):
            blob_parts += [str(r.get("source", "")), str(r.get("note") or "")]
    blob = " ".join(blob_parts)

    for i, s in enumerate(sessions or []):
        if not isinstance(s, dict):
            continue
        deck = s.get("usedDeck")
        if not nonempty(deck):
            continue
        rep.checked += 1
        stem = os.path.splitext(os.path.basename(deck))[0]
        if (stem and stem in blob) or deck in blob:
            rep.info("session.usedDeck",
                     "sessions[%d].usedDeck=%r 已在 unarchivedMaterials / "
                     "Source Register 留痕" % (i, deck))
        else:
            rep.warn("session.usedDeckNotRegistered",
                     "sessions[%d].usedDeck=%r 在 unarchivedMaterials 与 "
                     "Source Register 里都找不到对应条目"
                     "——课堂实际用了另一份课件，必须留痕" % (i, deck))


def check_warnings(man, rep):
    warns = man.get("warnings")
    if not isinstance(warns, list):
        rep.error("warnings.type", "warnings 必须是数组")
        return
    for i, w in enumerate(warns):
        rep.checked += 1
        if not nonempty(w):
            rep.error("warnings.entry", "warnings[%d] 不是非空字符串" % i)
    if warns:
        rep.warn("manifest.warnings",
                 "warnings 非空（%d 条）——每条都是「需要人工判断」的事实，逐条列出："
                 % len(warns))
        for i, w in enumerate(warns):
            rep.info("manifest.warnings", "  warnings[%d] %s" % (i, w))
    else:
        rep.info("manifest.warnings", "warnings 为空（干净交付）")


# ---------------------------------------------------------------- schema

def schema_validate(man, schema_path, rep):
    if not schema_path:
        return
    rep.checked += 1
    if not os.path.isfile(schema_path):
        rep.warn("schema.missing", "schema 文件不存在，跳过：%s" % schema_path)
        return
    try:
        import jsonschema
    except ImportError:
        rep.warn("schema.noJsonschema",
                 "未安装 jsonschema，跳过 schema 校验：pip install jsonschema")
        return
    try:
        with open(schema_path, encoding="utf-8") as fh:
            schema = json.load(fh)
    except json.JSONDecodeError as exc:
        rep.error("schema.badJson", "schema 文件不是合法 JSON：%s" % exc)
        return
    try:
        jsonschema.validate(man, schema)
        rep.info("schema.validate", "通过 %s" % os.path.basename(schema_path))
    except Exception as exc:
        rep.error("schema.validate", "不符合 schema：%s" % exc)


# ---------------------------------------------------------------- 主流程

def run(manifest_path, schema_path=None):
    rep = Report()
    if not os.path.isfile(manifest_path):
        rep.error("manifest.missing", "找不到 manifest：%s" % manifest_path)
        return rep, None
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            man = json.load(fh)
    except json.JSONDecodeError as exc:
        rep.error("manifest.badJson", "不是合法 JSON：%s" % exc)
        return rep, None
    except OSError as exc:
        rep.error("manifest.unreadable", "读不了：%s" % exc)
        return rep, None

    if not isinstance(man, dict):
        rep.error("manifest.type", "manifest 顶层必须是对象")
        return rep, man

    schema_validate(man, schema_path, rep)
    check_top(man, rep)
    sessions = check_source(man, rep)
    check_slides(man, rep, sessions)
    check_pdf(man, rep)
    check_register(man, rep)
    check_unarchived(man, rep)
    check_used_deck(man, rep, sessions)
    check_warnings(man, rep)
    return rep, man


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="check_manifest.py",
        description="只读校验 manifest.json（素材清单契约 v1 最终版）")
    ap.add_argument("manifest", help="manifest.json 路径")
    ap.add_argument("--schema", default=None, help="manifest.schema.json 路径")
    ap.add_argument("--quiet", action="store_true", help="只打印 RESULT 行")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rep, _ = run(args.manifest, args.schema)

    if args.json:
        print(json.dumps({
            "manifest": args.manifest, "ok": rep.ok, "checked": rep.checked,
            "errors": rep.count("ERROR"), "warns": rep.count("WARN"),
            "findings": [{"level": l, "rule": r, "message": m}
                         for l, r, m in rep.findings],
        }, ensure_ascii=False, indent=2))
    elif args.quiet:
        print("RESULT: %s (ERROR %d, WARN %d)"
              % ("PASS" if rep.ok else "FAIL", rep.count("ERROR"), rep.count("WARN")))
    else:
        print(rep.render(os.path.basename(args.manifest)))

    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
