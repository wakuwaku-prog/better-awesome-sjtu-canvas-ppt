#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_vault_profile.py — 目标知识库画像生成器（L2 笔记层第 ① 步）

设计原则（违反即停）：
  1. **先读 <vault>/AGENTS.md，再扫目录实况。** AGENTS.md 是唯一权威。
  2. **冲突时以 AGENTS.md 为准**，并把冲突写进 `warnings`（不静默选一边）。
  3. **不猜。** 读不出来的字段留 `null`（或省略可省略项），并在 `_unresolved` 里
     记下字段名与原因。绝不用"常识"补齐。
  4. 产出恒为 `reviewedByHuman: false`。人工复核前只允许只读操作。

三库实测差异（本脚本存在的理由）：
  | 维度            | 心理与精神健康   | 细胞生物学和组织学 | 有机化学        |
  |-----------------|------------------|--------------------|-----------------|
  | frontmatter 键  | type             | note_type          | type            |
  | 页图默认        | 内嵌             | 内嵌               | 内嵌            |
  | 页图宽度        | 540              | 760                | 540             |
  | 页图块 callout  | [!example] 展开  | 无 example 约定    | [!example]+ 展开|
  | V## 默认创建    | 否               | 否                 | 未找到          |
  | 媒体政策        | 转写入库/音视频不入库 | 三者都不入库（.gitignore 硬拦） | 转写入库/音视频不入库 |
  | Git 严格度      | status+fetch+pull | status+fetch+pull  | 未找到          |

用法：
  python build_vault_profile.py "<vault root>" [-o <out.json>] [--print] [--validate]

退出码：0 成功；2 参数/根目录问题；3 写盘失败。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# 目录定位：用"编号前缀 + 关键词"命中，不写死三库的具体目录名
# ---------------------------------------------------------------------------

# (profile 字段, 编号前缀候选, 关键词候选)
DIR_RULES: List[Tuple[str, List[str], List[str]]] = [
    ("lecture",      ["02"], ["课堂笔记", "课堂筆記"]),
    ("deck",         ["01"], ["课程资料", "课件"]),
    ("timeline",     [],     ["回放", "时间线"]),
    ("concept",      ["03"], ["核心概念"]),
    ("method",       ["05"], ["实验与方法", "方法"]),
    ("disorder",     ["04"], ["精神障碍"]),
    ("assessment",   ["06", "08"], ["复习与考核", "课程考核", "复习", "互动练习"]),
    ("explainer",    ["10", "05"], ["讲解 HTML", "讲解稿"]),
    ("quizBank",     [],     ["刷题工具", "题库"]),
    ("attachments",  ["09"], ["附件"]),
    ("templates",    ["99"], ["模板"]),
    ("scratch",      [],     ["_待整理", "_scripts"]),
]


def _listdir(root: str) -> List[str]:
    try:
        return sorted(os.listdir(root))
    except OSError:
        return []


def _find_dir(root: str, prefixes: List[str], keywords: List[str]) -> Optional[str]:
    """在根目录下找一个匹配的目录名。

    两轮：① 编号前缀 + 关键词都命中（最可靠）；② 仅关键词命中。
    prefixes 为空时退化为纯关键词匹配。
    """
    names = [n for n in _listdir(root) if os.path.isdir(os.path.join(root, n))]
    if prefixes:
        for n in names:
            for p in prefixes:
                if n.startswith(p) and any(k in n for k in keywords):
                    return n
    for n in names:
        if any(k in n for k in keywords):
            return n
    return None


def _find_file(root: str, rel_candidates: List[str]) -> Optional[str]:
    for rel in rel_candidates:
        if os.path.isfile(os.path.join(root, rel)):
            return rel
    return None


def _find_source_register(root: str, att_dir: Optional[str]) -> Optional[str]:
    """Source Register 的实况路径。三库文件名不同：Source Register.md / 资料登记表。"""
    for dp, dn, fn in os.walk(root):
        if any(seg in dp for seg in (os.sep + ".git", os.sep + ".obsidian")):
            continue
        depth = dp[len(root):].count(os.sep)
        if depth > 3:
            dn[:] = []
            continue
        for f in fn:
            if not f.endswith(".md"):
                continue
            low = f.lower()
            if "source register" in low or "资料登记" in f:
                return os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/")
    return None


# ---------------------------------------------------------------------------
# AGENTS.md 解析：规则优先于目录实况
# ---------------------------------------------------------------------------

RE_FRONTMATTER_KEYS = re.compile(
    r"YAML\s*frontmatter[^。\n]*?记录\s*(?P<keys>[^；。\n]+)", re.I
)
RE_NOTE_TYPE_KEYS = re.compile(
    r"(?:记录|使用)\s*`(?P<keys>[^`]*(?:note_type|type)[^`]*)`"
)


def _all_bullet_text(md: str) -> List[str]:
    out = []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ")):
            out.append(s[2:].strip())
    return out


def _parse_agents_md(md: str) -> Dict[str, Any]:
    """从 AGENTS.md 文本抽取本脚本关心的规则。

    每一条都保留 `evidence`（命中的原文行），便于人工复核与冲突判定。
    抽不到的字段返回 None —— 调用方负责写进 `_unresolved`，**不猜**。
    """
    rules: Dict[str, Any] = {
        "frontmatterRequired": None,
        "frontmatterKey": None,
        "createTimelineByDefault": None,
        "archiveVideo": None,
        "archiveAudio": None,
        "archiveTranscript": None,
        "registerDeletedMedia": None,
        "gitRequireStatus": None,
        "gitRequireFetch": None,
        "gitPullOnlyWhenClean": None,
        "gitForbidden": None,
        "shared": None,
        "naming": {},
        "evidenceTags": None,
        "imageEmbedSyntax": None,
        "preferEmbeddedPageImage": None,
        "widths": {},
        "callouts": {},
        "courseCode": None,
        "courseName": None,
        "excluded": [],
        "evidence": {},
    }
    ev: Dict[str, str] = {}

    def note(key: str, line: str) -> None:
        ev.setdefault(key, line.strip()[:240])

    # --- frontmatter 字段名与必填集 ---
    for line in md.splitlines():
        m = RE_FRONTMATTER_KEYS.search(line)
        if m:
            keys = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", m.group("keys"))
            if keys:
                rules["frontmatterRequired"] = keys
                if "note_type" in keys:
                    rules["frontmatterKey"] = "note_type"
                elif "type" in keys:
                    rules["frontmatterKey"] = "type"
                note("frontmatter", line)
                break

    # --- 课程代码 / 名称 ---
    # 只认**标题行**里的课程代码，或显式的「课程代码：」标签。
    # **不要在全库里搜 HLT####G**——有机化学库的 AGENTS.md 把 `HLT3202G-01-…`
    # 当作「格式参照的另一个仓库」提到，全库搜索会把别的课程代码当成本库的。
    m = re.search(r"课程代码[：:]\s*([A-Za-z0-9\-]+)", md)
    if m:
        rules["courseCode"] = m.group(1)
        note("courseCode", m.group(0))
    else:
        m = re.search(r"^#\s+AGENTS\.md\s*[—\-–]\s*(HLT[0-9]{4}G)\s+(.+)$", md, re.M)
        if m:
            rules["courseCode"] = m.group(1)
            rules["courseName"] = m.group(2).strip()
            note("courseCode", m.group(0))
        else:
            for line in md.splitlines():
                if re.match(r"^#{1,3}\s", line) and "AGENTS" not in line:
                    m2 = re.search(r"HLT[0-9]{4}G", line)
                    if m2:
                        rules["courseCode"] = m2.group(0)
                        note("courseCode", line)
                        break
    if rules["courseName"] is None:
        m = re.search(r"课程名称[：:]\s*([^\n（(]+)", md)
        if m:
            rules["courseName"] = m.group(1).strip()
            note("courseName", m.group(0))

    # --- 共享仓库 ---
    for line in md.splitlines():
        if re.search(r"(多名学生|多人|协作者|共同维护|共享仓库|由多名)", line):
            rules["shared"] = True
            note("shared", line)
            break

    # --- V## 是否默认创建 ---
    for line in md.splitlines():
        if "V##" in line and re.search(r"(不默认|仅按需|只有.*明确要求|仅在学习者明确)", line):
            rules["createTimelineByDefault"] = False
            note("timeline", line)
            break
        if "V##" in line and re.search(r"(默认建立|默认创建)", line) and "不默认" not in line:
            rules["createTimelineByDefault"] = True
            note("timeline", line)
            break

    # --- 媒体政策 ---
    for line in md.splitlines():
        if re.search(r"(原始课堂视频|课堂视频|原始视频|视频).{0,40}(临时处理输入|只作临时|处理后删除|不进入)", line):
            rules["archiveVideo"] = False
            note("archiveVideo", line)
        if re.search(r"(音频|音轨|AAC/WAV).{0,40}(临时处理输入|只作临时|处理后删除|不进入|删除)", line):
            rules["archiveAudio"] = False
            note("archiveAudio", line)
        if re.search(r"(转写|字幕|OCR/JSON)[^\n]{0,40}(放在|进入|登记|入库|留档|长期)", line):
            rules["archiveTranscript"] = True
            note("archiveTranscript", line)
        if re.search(r"不(为已经删除的媒体|建立指向不存在媒体的链接)", line):
            rules["registerDeletedMedia"] = True
            note("registerDeletedMedia", line)

    # --- Git 严格度 ---
    for line in md.splitlines():
        if re.search(r"git status", line):
            rules["gitRequireStatus"] = True
            note("gitRequireStatus", line)
        if re.search(r"git fetch", line):
            rules["gitRequireFetch"] = True
            note("gitRequireFetch", line)
        if re.search(r"pull --ff-only", line) and re.search(r"干净", line):
            rules["gitPullOnlyWhenClean"] = True
            note("gitPull", line)
        m = re.search(r"禁止\s*`?(git reset --hard[^。\n]*)", line)
        if m:
            rules["gitForbidden"] = m.group(1).strip()
            note("gitForbidden", line)

    # --- 图片嵌入语法与"默认内嵌页图" ---
    for line in md.splitlines():
        if "![[" in line and re.search(r"(嵌入|页图)", line):
            rules["imageEmbedSyntax"] = "![[{path}|{width}]]"
            note("imageEmbedSyntax", line)
        if "#page=" in line and re.search(r"(点击无效|无效|不显示)", line):
            rules["preferEmbeddedPageImage"] = True
            note("preferEmbedded", line)

    # --- 排除目录（不进共享范围） ---
    for line in md.splitlines():
        m = re.search(r"`(0[0-9] [^`]*?)`\s*(?:整个目录)?已(?:由|被)", line)
        if m:
            rules["excluded"].append(m.group(1).strip())
            note("excluded", line)
        m2 = re.search(r"`(/[^`]+/)`", line)
        if m2 and ".gitignore" in line:
            rules["excluded"].append(m2.group(1).strip("/"))

    # --- 证据标记集 ---
    for line in md.splitlines():
        if "区分" not in line:
            continue
        tags = re.findall(r"\[([^\]\n]+)\]", line)
        tags = [t for t in tags if t and not t.startswith("!")]
        if len(tags) >= 4:
            rules["evidenceTags"] = tags
            note("evidenceTags", line)
            break

    # --- 命名规则 ---
    # AGENTS.md 里两种写法都实测存在：
    #   ① `课堂笔记：`L## — 中文主题 English Topic``（细胞库）
    #   ② `- `L## — 中文主题 English Topic`：正式课堂笔记。`（心理库）
    # 要求串里必须含 ` — `（破折号），否则会把 `02 课堂笔记/L##` 这类**目录引用**
    # 误当成命名规则（心理库实测踩过）。非贪婪 `[^`\n]*?` 防止吃到行尾另一个反引号。
    for line in md.splitlines():
        m = re.search(r"课堂笔记[：:]?[^`\n]*?`([^`\n]*—[^`\n]+)`", line)
        if m:
            rules["naming"]["lecture"] = m.group(1)
        m = re.search(r"课件笔记[：:]?[^`\n]*?`([^`\n]*—[^`\n]+)`", line) or \
            re.search(r"课件逐页索引[：:]?[^`\n]*?`([^`\n]*—[^`\n]+)`", line)
        if m:
            rules["naming"]["deck"] = m.group(1)
        m = re.search(r"回放复核页[：:]?[^`\n]*?`([^`\n]*—[^`\n]+)`", line)
        if m:
            rules["naming"]["timeline"] = m.group(1)
        m = re.search(r"概念(?:主)?笔记[：:]?[^`\n]*?`([^`\n]+)`", line)
        if m:
            cand = m.group(1)
            # 排除目录引用（如 `03 核心概念/`）——那不是命名规则。
            if not re.match(r"^[0-9]{2}\s", cand) and not cand.endswith("/"):
                rules["naming"]["concept"] = cand
        # 退路：只给前缀、没给完整命名串（心理库 V## 即此情形）。
        if "timeline" not in rules["naming"]:
            m = re.search(r"`(V##)[^`\n]*`", line)
            if m:
                rules["naming"]["timeline"] = m.group(1)
                rules["timelinePrefixOnly"] = True
        if "方括号" in line and re.search(r"(破坏|不使用|禁止)", line):
            rules["naming"]["forbiddenChars"] = ["[", "]"]

    # --- callout 约定（AGENTS.md 明说的才采信）---
    for line in md.splitlines():
        m = re.search(r"`>\s*\[!(\w+)\]([+-])?\s*(待核实|我的理解|重要)", line)
        if m:
            kind, fold, label = m.group(1), m.group(2) or "", m.group(3)
            if label == "待核实":
                rules["callouts"]["toVerify"] = f"[!{kind}]"
            elif label == "我的理解":
                rules["callouts"]["myInterpretation"] = f"[!{kind}]"
            elif label == "重要":
                rules["callouts"]["mustRemember"] = f"[!{kind}]"
            note("callouts", line)

    # --- 讲解 / 考题产物落点（AGENTS.md 提到的）---
    m = re.search(r"`(1[0-9] 讲解 HTML|0[0-9] 讲解稿)`", md)
    if m:
        rules["explainerDir"] = m.group(1)
    m = re.search(r"`([^`]*刷题工具[^`]*)`", md)
    if m:
        rules["quizBankDir"] = m.group(1)

    rules["evidence"] = ev
    return rules


# ---------------------------------------------------------------------------
# 目录实况扫描
# ---------------------------------------------------------------------------

RE_FM = re.compile(r"^---\r?\n(.*?)\r?\n---", re.S)
RE_EMBED_WIDTH = re.compile(r"!\[\[[^\]|]+\|(\d+)\]\]")
RE_EMBED_PLAIN = re.compile(r"!\[\[([^\]|]+?)\]\]")
RE_PAGE_LINK = re.compile(r"#page=\d+")
RE_CALLOUT = re.compile(r"^>\s*\[!(\w+)\]([+-])?", re.M)
RE_FM_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):", re.M)

# 结构信号（供画像 `structure` 块使用）
RE_H2 = re.compile(r"^##\s+(?P<title>.+?)\s*$", re.M)
RE_NUMBERED_H2 = re.compile(r"^##\s+[0-9一二三四五六七八九十]+[、.．)]", re.M)
RE_LIST_ITEM = re.compile(r"^\s*(?:[0-9]+[.、)]|[-*+])\s+\S", re.M)
RE_UNCHECKED = re.compile(r"^\s*[-*+]\s+\[ \]", re.M)
RE_CHECKED = re.compile(r"^\s*[-*+]\s+\[[xX]\]", re.M)

# 「中文总结」小节的候选标题关键词（跨三库实测写法）
SUMMARY_KEYWORDS = ("中文总结", "总结", "synthesis", "summary", "摘要", "本节要点", "要点")
# 「主动回忆」小节的候选标题关键词
RECALL_KEYWORDS = ("主动回忆", "回忆", "active recall", "recall", "自测", "复习题")
# 「待核实」小节的候选标题关键词
TOVERIFY_KEYWORDS = ("待核实", "to verify", "to-verify", "开放问题", "open question",
                     "未核实", "疑问")


def _section_bodies(text: str) -> List[Tuple[str, str]]:
    """把正文切成 (二级标题, 该节正文) 的列表。"""
    heads = [(m.start(), m.end(), m.group("title")) for m in RE_H2.finditer(text)]
    out: List[Tuple[str, str]] = []
    for i, (_s, e, title) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        out.append((title, text[e:end]))
    return out


def _hits(title: str, keywords) -> bool:
    low = title.lower()
    return any(k.lower() in low for k in keywords)



def _iter_md(root: str):
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in (".git", ".obsidian", ".trash", "node_modules")]
        for f in fn:
            if f.endswith(".md"):
                yield os.path.join(dp, f)


def _widths_in_dir(root: str, rel_dir: Optional[str]) -> Dict[str, Any]:
    """统计某个目录下 md 的图片嵌入宽度，并区分「不带宽度」的写法。

    三库实测：心理库 L 笔记统一 `|540`，细胞库统一 `|760`，有机化学库 L 笔记
    **完全不写宽度**。不带宽度是一种真实约定，不能当成缺失而随便补一个数。
    """
    out: Dict[str, Any] = {"widths": {}, "plainNoWidth": 0}
    if not rel_dir:
        return out
    d = os.path.join(root, rel_dir)
    if not os.path.isdir(d):
        return out
    c: Counter = Counter()
    for n in _listdir(d):
        if not n.endswith(".md"):
            continue
        t = open(os.path.join(d, n), encoding="utf-8", errors="replace").read()
        for w in RE_EMBED_WIDTH.findall(t):
            c[int(w)] += 1
        for target in RE_EMBED_PLAIN.findall(t):
            if target.startswith(("http://", "https://")):
                continue
            out["plainNoWidth"] += 1
    out["widths"] = dict(sorted(c.items(), key=lambda x: -x[1]))
    return out


def scan_vault(root: str) -> Dict[str, Any]:
    """扫目录实况。所有结论都带计数，供 warnings 与人工复核。

    除了全局频次，本函数还收集**逐篇**的 frontmatter 键集与结构信号——
    因为「必填」是**分类型**的：L 笔记要 lecture_id，P 笔记要 deck，
    Concept 要 concept。用全局频次并集当必填集，会得到一份任何单篇笔记都
    满足不了的要求集（实测心理库 12 个全局必填字段 → 14 篇报 23 个 ERROR）。
    """
    fm_keys: Counter = Counter()
    type_vals: Counter = Counter()
    type_key_counter: Counter = Counter()
    widths: Counter = Counter()
    callouts: Counter = Counter()
    page_links = 0
    n_md = 0
    n_fm = 0
    notes: List[Dict[str, Any]] = []

    for path in _iter_md(root):
        n_md += 1
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue

        note_type: Optional[str] = None
        keys: List[str] = []
        m = RE_FM.search(text)
        if m:
            n_fm += 1
            body = m.group(1)
            keys = RE_FM_KEY.findall(body)
            for k in keys:
                fm_keys[k] += 1
            for tk in ("note_type", "type"):
                tm = re.search(rf"^{tk}:\s*(.+)$", body, re.M)
                if tm:
                    type_key_counter[tk] += 1
                    type_vals[f"{tk}={tm.group(1).strip()}"] += 1
                    if note_type is None:
                        note_type = tm.group(1).strip()

        # ── 结构信号：逐篇统计，供 `structure` 块按类型推断 ──────────────
        sections = _section_bodies(text)
        summary_titles = [t for t, _ in sections if _hits(t, SUMMARY_KEYWORDS)]
        recall_bodies = [b for t, b in sections if _hits(t, RECALL_KEYWORDS)]
        toverify_bodies = [b for t, b in sections if _hits(t, TOVERIFY_KEYWORDS)]
        numbered_h2 = len(RE_NUMBERED_H2.findall(text))
        recall_items = max((len(RE_LIST_ITEM.findall(b)) for b in recall_bodies), default=0)

        notes.append({
            "path": path,
            "noteType": note_type,
            "keys": keys,
            "hasSummary": bool(summary_titles),
            "summaryTitle": summary_titles[0] if summary_titles else None,
            "hasNumberedH2": numbered_h2 > 0,
            "recallItems": recall_items,
            "hasRecallSection": bool(recall_bodies),
            "hasToVerify": bool(toverify_bodies),
            "hasUncheckedToVerify": any(RE_UNCHECKED.search(b) for b in toverify_bodies),
        })

        for w in RE_EMBED_WIDTH.findall(text):
            widths[int(w)] += 1
        for c in RE_CALLOUT.findall(text):
            callouts[c[0] + c[1]] += 1
        page_links += len(RE_PAGE_LINK.findall(text))

    return {
        "mdCount": n_md,
        "frontmatterCount": n_fm,
        "frontmatterKeys": dict(fm_keys),
        "typeKeyCounter": dict(type_key_counter),
        "typeValues": dict(type_vals),
        "embedWidths": dict(sorted(widths.items(), key=lambda x: -x[1])),
        "callouts": dict(sorted(callouts.items(), key=lambda x: -x[1])),
        "pageLinkCount": page_links,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# 最小 JSON Schema 校验（无 jsonschema 时兜底：required / additionalProperties / type）
# ---------------------------------------------------------------------------

def _type_ok(value: Any, spec: Any) -> bool:
    if isinstance(spec, list):
        return any(_type_ok(value, s) for s in spec)
    return {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "null": lambda v: v is None,
    }.get(spec, lambda v: True)(value)


def minimal_validate(instance: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    errs: List[str] = []
    if "const" in schema and instance != schema["const"]:
        errs.append(f"{path}: 期望 const={schema['const']!r}，实际 {instance!r}")
    if "type" in schema and not _type_ok(instance, schema["type"]):
        errs.append(f"{path}: 类型应为 {schema['type']}，实际 {type(instance).__name__}")
        return errs
    if "enum" in schema and instance not in schema["enum"]:
        errs.append(f"{path}: {instance!r} 不在 enum {schema['enum']}")
    if isinstance(instance, dict):
        for req in schema.get("required", []):
            if req not in instance:
                errs.append(f"{path}: 缺必填字段 {req!r}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for k in instance:
                if k not in props:
                    errs.append(f"{path}: 未定义字段 {k!r}")
        for k, v in instance.items():
            if k in props:
                errs += minimal_validate(v, props[k], f"{path}.{k}")
    if isinstance(instance, list) and "items" in schema:
        for i, v in enumerate(instance):
            errs += minimal_validate(v, schema["items"], f"{path}[{i}]")
    return errs


def load_schema() -> Optional[Dict[str, Any]]:
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "..", "schemas", "vault-profile.schema.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return None


# ---------------------------------------------------------------------------
# 组装画像
# ---------------------------------------------------------------------------

def _infer_naming_from_dir(root: str, rel_dir: Optional[str], prefix: str) -> Optional[str]:
    """目录里确有 `<prefix>## — …` 的文件，才敢报这个命名模板；否则 None。"""
    if not rel_dir:
        return None
    d = os.path.join(root, rel_dir)
    if not os.path.isdir(d):
        return None
    hit = [n for n in _listdir(d) if n.startswith(prefix) and "—" in n and n.endswith(".md")]
    if not hit:
        return None
    return f"{prefix}## — 中文主题 English Topic"


# ── frontmatter 必填：分类型推断 ──────────────────────────────────────────────
# 「必填」是**分类型**的。全局并集是错的：心理库实测并集 12 个字段
# （type/course_code/tags/status/source/date/title/lecture/concept/lecture_id/
# lecture_date/source_location），而 Concept 页有 concept 却没有 lecture_id、
# P 页有 deck 却没有 lecture_date —— 没有任何单篇笔记能同时满足这 12 个，
# 于是 14 篇笔记报 23 个 ERROR，全是假红灯。

REQUIRED_RATE = 0.90   # 某字段在**该类型**的笔记里出现率 ≥90% 才算该类必填
MIN_SAMPLES_FOR_INFERENCE = 3   # 样本少于这么多篇，**不推断必填集**（宁可少报）


def infer_required_per_note_type(notes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按笔记类型推断必填字段。

    **字段名原样保留**：不规范化、不套用别的库的名字。心理库的 L 笔记实际用
    `lecture`（不是 `lecture_id`）、P 笔记用 `deck`（不是 `deck_id`）——
    写「规范名」会让每篇 L 笔记都被报「缺 lecture_id」，又是一批假红灯。

    样本不足（< MIN_SAMPLES_FOR_INFERENCE 篇）的类型**留空数组**，不做推断：
    只有 1 篇笔记的类型，它的字段集就是那一篇的偶然写法，据此宣布必填是造红灯。

    返回 `perType`（类型 → 必填字段）与 `detail`（逐类型样本数、逐字段出现率）。
    """
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    untyped: List[Dict[str, Any]] = []
    for n in notes:
        if n.get("noteType"):
            by_type.setdefault(n["noteType"], []).append(n)
        else:
            untyped.append(n)

    out: Dict[str, List[str]] = {}
    detail: Dict[str, Any] = {}
    low_sample: List[Dict[str, Any]] = []
    for t, group in sorted(by_type.items()):
        total = len(group)
        if total == 0:
            continue
        cnt: Counter = Counter()
        for n in group:
            for k in set(n["keys"]):
                cnt[k] += 1
        rates = {k: round(c / total, 3) for k, c in cnt.most_common()}
        if total < MIN_SAMPLES_FOR_INFERENCE:
            out[t] = []
            detail[t] = {"samples": total, "required": [], "rates": rates,
                         "skipped": f"样本仅 {total} 篇，不足 {MIN_SAMPLES_FOR_INFERENCE} 篇，未推断必填集"}
            low_sample.append({"type": t, "samples": total,
                               "observedKeys": sorted(cnt)})
            continue
        req = sorted(k for k, c in cnt.items() if c / total >= REQUIRED_RATE)
        out[t] = req
        detail[t] = {"samples": total, "required": req, "rates": rates}
    return {"perType": out, "detail": detail,
            "untypedSamples": len(untyped), "lowSample": low_sample}


def infer_global_required(per_type: Dict[str, List[str]],
                          notes: List[Dict[str, Any]]) -> List[str]:
    """全局必填 = **各类型必填集的交集**（每篇都有的字段）。

    不用「出现频次前 N 项」——那个做法把分类型字段混进全局要求里。
    只对**推断成功**的类型取交集：样本不足的类型必填集是空数组，
    若把它们算进来会让交集恒为空。
    """
    sets = [set(v) for v in per_type.values() if v]
    if not sets:
        return []
    return sorted(set.intersection(*sets))


# ── structure 块：复用校验器硬编码启发式会误报，必须由画像驱动 ──────────────
# 实测：心理库 14 篇被复用校验器报 22 个问题，多数因为它只认固定小节名，
# 且对 P 页面（课件逐页索引）也要求主动回忆题——P 页面本来就没有回忆题。

STRUCTURE_PRESENT_RATE = 0.80   # 某类型 ≥80% 的笔记有该结构，才算该类型要求它


def infer_structure(notes: List[Dict[str, Any]],
                    per_type: Dict[str, List[str]]) -> Dict[str, Any]:
    """按类型推断结构要求。

    键优先用 **frontmatter 类型值**（`lecture-note` / `ppt-note` / `concept`）——
    那才是「类型」的正身。编号前缀（L/P/V/T）只作为**补充键**，且只在
    「该前缀下确实有 `<前缀>##` 形式的笔记」时才加：
    心理库的 `02 课堂笔记/README — Lecture Notes.md` 文件名不以 `L` + 数字开头，
    若把它算进 `L` 组，`L` 的最小值会被拉成 0，与 `lecture-note: 3` 自相矛盾。
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}

    # ① 主键：frontmatter 类型值
    for n in notes:
        if n.get("noteType"):
            groups.setdefault(n["noteType"], []).append(n)

    # ② 补充键：编号前缀，只收 `<前缀>##` 形式的真笔记
    pref_groups: Dict[str, List[Dict[str, Any]]] = {}
    for n in notes:
        m = re.match(r"^([LPVT])\d", os.path.basename(n["path"]))
        if m:
            pref_groups.setdefault(m.group(1), []).append(n)
    for key, group in pref_groups.items():
        # 该前缀下所有笔记都有 frontmatter 类型 → 主键已覆盖，不再加会矛盾的补充键
        if all(n.get("noteType") for n in group):
            continue
        groups.setdefault(key, group)

    recall: Dict[str, int] = {}
    toverify: Dict[str, bool] = {}
    unchecked: Dict[str, bool] = {}
    summary_titles: Counter = Counter()
    any_numbered = False
    n_summary = 0
    n_total = 0

    for key, group in sorted(groups.items()):
        total = len(group)
        if not total:
            continue
        recall_counts = [n["recallItems"] for n in group]
        # 用**最小值**：要求必须是所有样本都满足的下界，否则会把某篇的写法
        # 变成对全类型的硬要求（正是这次缺陷的成因）。
        recall[key] = min(recall_counts) if recall_counts else 0
        toverify[key] = (sum(1 for n in group if n["hasToVerify"]) / total
                         >= STRUCTURE_PRESENT_RATE)
        unchecked[key] = (sum(1 for n in group if n["hasUncheckedToVerify"]) / total
                          >= STRUCTURE_PRESENT_RATE)

    for n in notes:
        n_total += 1
        if n["hasSummary"]:
            n_summary += 1
            if n.get("summaryTitle"):
                summary_titles[n["summaryTitle"]] += 1
        if n["hasNumberedH2"]:
            any_numbered = True

    # 小节别名：只取**真正像小节名**的标题。
    # 实测心理库的 `## 四、一张总结图（怎么问）` 这类是内容标题、不是"中文总结"小节名；
    # 直接把它们塞进别名表，会让校验器以为这些是通用小节名。
    aliases: List[str] = []
    for t, _c in summary_titles.most_common(20):
        clean = re.sub(r"^[0-9一二三四五六七八九十]+[、.．)]\s*", "", t).strip()
        if not clean or len(clean) > 28:
            continue          # 过长的一律不当别名（多为内容标题）
        if not _hits(clean, SUMMARY_KEYWORDS):
            continue          # 不含总结类关键词的，不认
        if clean not in aliases:
            aliases.append(clean)
    for kw in ("中文总结", "本节要点", "Chinese synthesis"):
        if kw not in aliases:
            aliases.append(kw)

    # 认定方式：所有有总结的笔记都用编号标题 → any-numbered-heading；
    # 否则若有非编号标题命中别名 → heading-alias；都没有 → not-required
    if n_summary == 0:
        detection = "not-required"
    elif any_numbered and all(re.match(r"^[0-9一二三四五六七八九十]+[、.．)]", t)
                              for t in summary_titles):
        detection = "any-numbered-heading"
    else:
        detection = "heading-alias"

    # 复用校验器发现类别的降级：只列有明确映射的类别
    demote: List[str] = []
    if n_summary and detection != "not-required":
        demote.append("summary")
    if any(v > 0 for v in recall.values()):
        demote.append("recall")
    if any(toverify.values()):
        demote.append("to-verify")
    if per_type:
        demote.append("frontmatter")

    return {
        "summarySectionAliases": aliases,
        "summaryDetection": detection,
        "recallQuestions": recall,
        "requireToVerifyList": toverify,
        "requireUncheckedToVerifyItem": unchecked,
        "demoteReusedFindings": demote,
        "_detail": {
            "groups": {k: len(v) for k, v in sorted(groups.items())},
            "summaryTitles": dict(summary_titles.most_common(10)),
            "notesWithSummary": n_summary,
            "notesTotal": n_total,
        },
    }


def build(root: str) -> Dict[str, Any]:
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise SystemExit(f"[build_vault_profile] 根目录不存在：{root}")

    warnings: List[str] = []
    unresolved: List[Dict[str, str]] = []

    def miss(field: str, why: str) -> None:
        unresolved.append({"field": field, "reason": why})

    agents_rel = _find_file(root, ["AGENTS.md", "AGENT.md"])
    agents_md = ""
    if agents_rel:
        agents_md = open(os.path.join(root, agents_rel), encoding="utf-8", errors="replace").read()
    else:
        warnings.append("未找到 AGENTS.md；本 skill 的第一读缺失，画像只能依赖目录实况——建议人工补齐。")

    rules = _parse_agents_md(agents_md)
    scan = scan_vault(root)
    name = os.path.basename(root.rstrip("\\/"))

    # ---- dirs ----
    dirs: Dict[str, Any] = {}
    for field, prefixes, keywords in DIR_RULES:
        found = _find_dir(root, prefixes, keywords)
        if field == "attachments":
            att_root = found
            dirs["attachments"] = (
                f"{att_root}/附件" if att_root and os.path.isdir(os.path.join(root, att_root, "附件"))
                else (att_root or "09 参考资料/附件")
            )
            continue
        dirs[field] = found

    dirs["sourceRegister"] = _find_source_register(root, dirs.get("attachments"))
    dirs["explainer"] = dirs.get("explainer") or rules.get("explainerDir")
    dirs["quizBank"] = None
    if rules.get("quizBankDir"):
        qb = rules["quizBankDir"].split("/")[0]
        dirs["quizBank"] = qb if os.path.isdir(os.path.join(root, qb)) else None
    if dirs.get("quizBank") is None:
        # 库里可能用别的目录名放题库（如 08 课程考核 / 06 互动练习），只认目录实况。
        for cand in ("刷题工具", "题库"):
            if os.path.isdir(os.path.join(root, cand)):
                dirs["quizBank"] = cand
                break
    dirs["excluded"] = sorted(set(rules["excluded"]))

    if not dirs.get("lecture"):
        warnings.append("未定位到课堂笔记目录（02 课堂笔记）。L## 无法落盘，先人工确认。")
        miss("dirs.lecture", "目录实况里没有匹配『02 + 课堂笔记』的目录")
    for f in ("deck", "timeline", "concept", "method", "disorder", "assessment",
              "explainer", "quizBank", "templates", "scratch"):
        if dirs.get(f) is None:
            miss(f"dirs.{f}", "AGENTS.md 与目录实况都没有该产物的落点")

    # ---- 冲突：AGENTS.md 说有 V##，但目录里没有 ----
    if rules.get("createTimelineByDefault") is False:
        if dirs.get("timeline"):
            warnings.append(
                f"冲突：AGENTS.md 规定 V## 仅按需建立，但目录里存在 {dirs['timeline']}/；"
                "以 AGENTS.md 为准，V## 默认不创建。"
            )
    # ---- 冲突：AGENTS.md 的命名规则 vs 目录实况 ----
    naming_lecture = rules["naming"].get("lecture")
    if naming_lecture and dirs.get("lecture"):
        sample = [n for n in _listdir(os.path.join(root, dirs["lecture"])) if n.endswith(".md")]
        pref = re.match(r"^([A-Za-z]+)", naming_lecture)
        if pref and sample and not any(n.startswith(pref.group(1)) for n in sample):
            warnings.append(
                f"冲突：AGENTS.md 命名规则为『{naming_lecture}』，但 {dirs['lecture']}/ 下"
                f"没有以 {pref.group(1)} 开头的文件；以 AGENTS.md 为准。"
            )

    # ---- frontmatter ----
    fm_key = rules["frontmatterKey"]
    if fm_key is None:
        tk = scan["typeKeyCounter"]
        if tk:
            fm_key = max(tk, key=lambda k: tk[k])
            warnings.append(
                f"AGENTS.md 未明说 frontmatter 字段名；按目录实况取 {fm_key!r}"
                f"（实测计数 {tk}）。需人工复核。"
            )
        else:
            miss("frontmatter.required", "AGENTS.md 未列字段名，目录里也没有可解析的 frontmatter")
    frontmatter: Dict[str, Any] = {}

    # 分类型推断：这是「必填」的正确粒度。
    req_by_type = infer_required_per_note_type(scan["notes"])
    per_type_req: Dict[str, List[str]] = req_by_type["perType"]
    inferred_required: List[str] = []

    if rules["frontmatterRequired"]:
        # AGENTS.md 逐字列出了字段 → 采信，但**仍然按类型分摊**：
        # AGENTS.md 给的是「该库会用的字段」，不是「每篇都要有的字段」。
        frontmatter["required"] = list(rules["frontmatterRequired"])
        frontmatter["requiredIsInferred"] = False
        if per_type_req:
            frontmatter["requiredPerNoteType"] = per_type_req
        warnings.append(
            "AGENTS.md 逐字列出了 frontmatter 字段，已作为全局 `required`；"
            f"按类型推断的 `requiredPerNoteType` 另有 {len(per_type_req)} 类。"
            "两者语义不同，不要混用。"
        )
    elif scan["frontmatterKeys"]:
        inferred_required = infer_global_required(per_type_req, scan["notes"])
        frontmatter["required"] = inferred_required
        frontmatter["requiredIsInferred"] = True
        frontmatter["requiredPerNoteType"] = per_type_req
        warnings.append(
            f"⚠ frontmatter.required 是**推断值**（requiredIsInferred=true）："
            f"取各笔记类型的**交集**，共 {len(inferred_required)} 个字段 "
            f"{inferred_required}。"
            "AGENTS.md 未逐字列出，**复核前不要当硬要求**——"
            "`reviewedByHuman=false` 且 `requiredIsInferred=true` 时，"
            "校验器只能报 WARN/INFO，不得据此报 ERROR。"
        )
        old = len(scan["frontmatterKeys"])
        warnings.append(
            f"对照：旧策略（出现频次前 12 项）会得到 {min(old, 12)} 个全局必填字段，"
            "把 title/lecture/concept/lecture_date/source_location 这类**分类型**字段"
            "混进全局要求，得到一份任何单篇笔记都满足不了的要求集"
            "（实测心理库 14 篇报 23 个 ERROR，全是假红灯）。"
            f"新策略按类型推断，全局取交集后只剩 {len(inferred_required)} 个。"
        )
        nonempty = {t: v for t, v in per_type_req.items() if v}
        warnings.append(
            f"frontmatter.requiredPerNoteType 覆盖 {len(nonempty)} 个类型"
            f"（共 {len(per_type_req)} 个类型有 frontmatter）。"
            "**字段名一律取自该库实际笔记，未做规范化**"
            "（例：心理库 L 笔记实际用 `lecture`，不是 `lecture_id`；"
            "P 笔记实际用 `deck`，不是 `deck_id`）。"
            "下游校验必须用这些实际键名——写「规范名」会造出假红灯。"
        )
        low = req_by_type.get("lowSample") or []
        if low:
            warnings.append(
                f"⚠ 有 {len(low)} 个类型的样本不足 {MIN_SAMPLES_FOR_INFERENCE} 篇，"
                "**未推断必填集**（留空数组：宁可少报，不要造出假红灯）："
                + "、".join(f"{x['type']}({x['samples']} 篇)" for x in low[:12])
                + "。这些类型的实际字段可在 `_scan.notes` 里逐篇查看。"
            )
        if not inferred_required:
            warnings.append(
                "各类型必填集的交集为空——说明该库的 frontmatter 字段几乎全部分类型。"
                "这是**正常结果**，不是推断失败；请以 `requiredPerNoteType` 为准。"
            )
    else:
        miss("frontmatter.required", "两路都没有证据")
        frontmatter["requiredIsInferred"] = False

    frontmatter["optional"] = sorted(
        set(scan["frontmatterKeys"]) - set(frontmatter.get("required", []))
    )
    # perNoteType 保留原语义：类型 → **额外**字段（不在 requiredPerNoteType 里的）
    frontmatter["perNoteType"] = {
        t: sorted(set(req_by_type["detail"].get(t, {}).get("rates", {}))
                  - set(per_type_req.get(t, [])))
        for t in per_type_req
    }
    statuses = sorted({
        m for m in re.findall(r"status:\s*([A-Za-z0-9_\-]+)", agents_md)
    })
    frontmatter["statusEnum"] = statuses or None
    if not statuses:
        miss("frontmatter.statusEnum", "AGENTS.md 里没有可枚举的 status 取值（目录实况只能给实例，不能给枚举）")

    # ---- structure（复用校验器的硬编码启发式会大面积误报，必须画像驱动）----
    structure = infer_structure(scan["notes"], per_type_req)
    if not structure["recallQuestions"]:
        miss("structure.recallQuestions", "没有任何可按类型分组的笔记样本")
    if structure["summaryDetection"] == "not-required":
        warnings.append(
            "structure.summaryDetection=not-required：目录里没有任何笔记含「中文总结」小节。"
            "**这是实测结果**，不是漏抽——请人工确认该库是否真的不要求这一节。"
        )
    if structure["recallQuestions"]:
        zero_keys = [k for k, v in structure["recallQuestions"].items() if v == 0]
        if zero_keys:
            warnings.append(
                "structure.recallQuestions 里这些类型记为 0："
                + "、".join(zero_keys[:10])
                + "。0 表示**该类型实测没有回忆题**（如 P 页面是课件逐页索引、"
                "本来就不含回忆题），**不是「没抽到」**。复用校验器若对这些类型"
                "也要求回忆题，属误报。"
            )


    # ---- mediaPolicy ----
    media_policy: Dict[str, Any] = {
        "archiveVideo": rules["archiveVideo"] if rules["archiveVideo"] is not None else False,
        "archiveAudio": rules["archiveAudio"] if rules["archiveAudio"] is not None else False,
        "archiveTranscript": rules["archiveTranscript"] if rules["archiveTranscript"] is not None else True,
        "archiveSlideImages": bool(scan["embedWidths"]) or bool(dirs.get("attachments")),
        "registerDeletedMedia": bool(rules["registerDeletedMedia"]),
    }
    for k in ("archiveVideo", "archiveAudio", "archiveTranscript"):
        if rules[k] is None:
            media_policy[k] = media_policy[k]
            miss(f"mediaPolicy.{k}", "AGENTS.md 未明说；取保守默认（媒体不入库），需人工复核")
    media_policy["note"] = rules["evidence"].get("archiveVideo") or None

    # ---- images ----
    # 宽度必须按目录分别实测：L 笔记与 P 页面可以是两个约定。
    lec_w = _widths_in_dir(root, dirs.get("lecture"))
    deck_w = _widths_in_dir(root, dirs.get("deck"))
    lecture_width = next(iter(lec_w["widths"]), None)
    deck_width = next(iter(deck_w["widths"]), None)
    if lecture_width is None and deck_width is not None:
        lecture_width = deck_width
        warnings.append(
            f"L 笔记目录里没有带宽度嵌入；lectureWidth 借用 P 页面的实测宽度"
            f"（{lecture_width}），需人工确认。"
        )
    if deck_width is None:
        deck_width = lecture_width
    images: Dict[str, Any] = {
        "embedSyntax": rules["imageEmbedSyntax"] or "![[{path}|{width}]]",
        "lectureWidth": lecture_width,
        "deckWidth": deck_width,
        "timelineWidth": None,
        "escapePipeInTables": True,  # 契约硬规则：表格内嵌图片必须写 \|
        "preferEmbeddedPageImage": True,  # 契约硬规则：心理库实测 #page= 点击无效
    }
    if lec_w["plainNoWidth"] and lecture_width is not None:
        warnings.append(
            f"{dirs.get('lecture')}/ 里有 {lec_w['plainNoWidth']} 处嵌入**不写宽度**"
            f"（`![[路径]]`），另有 {sum(lec_w['widths'].values())} 处写宽度 "
            f"{lecture_width}。两种写法并存；本 skill 统一按 "
            f"`![[路径|{lecture_width}]]` 产出。"
        )
    if lecture_width is None:
        # 有机化学库实测：L 笔记与 P 页面都**不写宽度**，只有别处的图写宽度。
        # 这是真实的第三种约定，不能替它编一个数。
        other = [w for w, _ in Counter(scan["embedWidths"]).most_common(3)]
        miss("images.lectureWidth",
             f"L 笔记与 P 页面都不写宽度（实测 {lec_w['plainNoWidth']} / {deck_w['plainNoWidth']} 处无宽度嵌入）；"
             f"全库其它位置的宽度为 {other}")
        warnings.append(
            "本库的图片嵌入**不写宽度**（`![[路径]]`）。模板与 gen_notes 将按 "
            "`![[路径]]` 原样产出；若学习者希望统一加宽度，需人工指定一个数值——本 skill 不替它决定。"
        )
    if scan["pageLinkCount"] and images["preferEmbeddedPageImage"]:
        warnings.append(
            f"目录实况里仍有 {scan['pageLinkCount']} 处 `#page=` 链接（多用于表格内文本链接）。"
            "契约规定**图片嵌入**默认内嵌页图、不用 `#page=`；这些文本链接不在改动范围内，"
            "但若学习者反馈同样点击无效，需一并处理。"
        )

    # ---- callouts ----
    callouts: Dict[str, Any] = {}
    ex_plus = scan["callouts"].get("example+", 0)
    ex_bare = scan["callouts"].get("example", 0)
    ex_fold = scan["callouts"].get("example-", 0)
    if ex_plus or ex_bare or ex_fold:
        callouts["slideBlock"] = "[!example]+" if (ex_plus or ex_fold) else "[!example]"
        if ex_fold:
            warnings.append(
                f"实测仍有 {ex_fold} 处折叠图片块 `[!example]-`。折叠会让图片默认不可见"
                "（有机化学库实测 72/72 篇可见图数为 0）；本 skill 一律产出展开语法。"
            )
    else:
        miss("callouts.slideBlock", "全库没有 example callout 实测；AGENTS.md 也未规定图片块用哪种 callout")
        warnings.append(
            "本库没有既有的课件页图 callout 约定。模板将使用契约硬规则 `[!example]+`（展开），"
            "需人工确认该库接受这一形式。"
        )
    callouts["mustRemember"] = rules["callouts"].get("mustRemember") or (
        "[!important]" if scan["callouts"].get("important") else None
    )
    callouts["toVerify"] = rules["callouts"].get("toVerify") or (
        "[!note]" if scan["callouts"].get("note") else None
    )
    callouts["myInterpretation"] = rules["callouts"].get("myInterpretation") or (
        "[!tip]" if scan["callouts"].get("tip") else None
    )
    callouts["quizAnswer"] = "[!success]-" if scan["callouts"].get("success-") else None
    callouts["abstract"] = "[!abstract]" if scan["callouts"].get("abstract") else None

    # ---- evidenceTags ----
    evidence_tags = rules["evidenceTags"] or []
    if not evidence_tags:
        miss("evidenceTags", "AGENTS.md 未列出该库的证据标记集")
    elif len(evidence_tags) < 4:
        warnings.append(f"证据标记集只抽到 {len(evidence_tags)} 个：{evidence_tags}；可能漏抽，需人工复核。")

    # ---- explainer / quiz ----
    explainer: Dict[str, Any] = {
        "enabled": bool(dirs.get("explainer")),
        "mode": "single-file-html" if dirs.get("explainer") else "none",
        "outDir": dirs.get("explainer"),
        "imageMaxEdgePx": None,
        "maxBytes": None,
        "imageWidthAttr": "max-width:min(720px,100%)",
        "dshUiFencesPerTurn": 6,
    }
    miss("explainer.imageMaxEdgePx", "无证据：AGENTS.md 与既有产物都没有给出缩小长边")
    miss("explainer.maxBytes", "无证据：未给出单文件体积上限")
    quiz: Dict[str, Any] = {
        "bankEnabled": bool(dirs.get("quizBank")),
        "bankSchemaVersion": 1,
        "outDir": dirs.get("quizBank"),
        "questionsPerSet": None,
        "correctOptionMustNotBeLongest": True,
        "shuffleAnswerPositions": True,
        "explanationsCiteSource": True,
    }
    miss("quiz.questionsPerSet", "无证据：AGENTS.md 未规定每组题量")

    # ---- git ----
    git_block: Dict[str, Any] = {
        "shared": bool(rules["shared"]) or bool(dirs["excluded"]),
        "requireStatusBeforeEdit": rules["gitRequireStatus"] is True,
        "requireFetchBeforeEdit": rules["gitRequireFetch"] is True,
        "pullOnlyWhenClean": True,
        "stageOnlyTargetFiles": True,
        "forbidden": [
            "git reset --hard", "git checkout -- .", "git clean",
            "git push --force", "git push --force-with-lease",
        ],
    }
    if rules["gitRequireStatus"] is None and rules["gitRequireFetch"] is None:
        warnings.append(
            "AGENTS.md 没有 Git 流程条款；git.requireStatusBeforeEdit / requireFetchBeforeEdit "
            "记为 false（**这是『未找到』而非『不要求』**）。编辑该库前请人工确认。"
        )
        miss("git.requireStatusBeforeEdit", "AGENTS.md 无 Git 流程条款")
        miss("git.requireFetchBeforeEdit", "AGENTS.md 无 Git 流程条款")

    # ---- 组装 ----
    profile: Dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "vault": {
            "root": root,
            "name": name,
            "courseCode": rules["courseCode"],
            "courseName": rules["courseName"],
            "agentsMd": agents_rel,
            "shared": bool(rules["shared"]) or bool(dirs["excluded"]),
            "generatedAt": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "reviewedByHuman": False,
        },
        "dirs": dirs,
        "naming": {
            "lecture": (naming_lecture
                        or _infer_naming_from_dir(root, dirs.get("lecture"), "L")),
            "deck": (rules["naming"].get("deck")
                     or _infer_naming_from_dir(root, dirs.get("deck"), "P")),
            "timeline": rules["naming"].get("timeline"),
            "concept": rules["naming"].get("concept") or "Concept — English Term",
            "forbiddenChars": rules["naming"].get("forbiddenChars", ["[", "]"]),
        },
        "frontmatter": frontmatter,
        "structure": {k: v for k, v in structure.items() if not k.startswith("_")},
        "mediaPolicy": media_policy,
        "images": images,
        "callouts": callouts,
        "evidenceTags": evidence_tags,
        "explainer": explainer,
        "quiz": quiz,
        "git": git_block,
    }
    if rules["courseCode"] is None:
        miss("vault.courseCode", "AGENTS.md 未给课程代码（有机化学库属课程系列而非单一课程代码）")

    # ---- 内部一致性检查（不是 schema 校验，是"别自相矛盾"）----
    if not media_policy["archiveTranscript"] and media_policy["archiveSlideImages"]:
        warnings.append("媒体政策自相矛盾：转写不入库但页图入库。需人工确认。")

    # ---- 未找到项汇总 ----
    if unresolved:
        warnings.append(
            "未找到（未用常识补齐）的字段：" + "、".join(u["field"] for u in unresolved)
        )

    profile["_scan"] = scan          # 证据台账，供人工复核；不进 schema 的正式字段
    profile["_unresolved"] = unresolved
    profile["_evidence"] = rules["evidence"]
    profile["_warnings"] = warnings
    return profile


def strip_meta(profile: Dict[str, Any]) -> Dict[str, Any]:
    """产出严格符合 schema 的画像（去掉 `_` 前缀的证据台账）。"""
    return {k: v for k, v in profile.items() if not k.startswith("_")}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="生成目标 Obsidian 知识库画像（先读 AGENTS.md，再扫目录实况）"
    )
    ap.add_argument("vault", help="知识库根目录")
    ap.add_argument("-o", "--out", default=None,
                    help="输出路径；默认 <vault>/_scripts/vault-profile.json")
    ap.add_argument("--print", dest="do_print", action="store_true",
                    help="把画像打到 stdout（不写盘）")
    ap.add_argument("--with-meta", action="store_true",
                    help="输出时保留 _scan/_unresolved/_evidence/_warnings 证据台账")
    ap.add_argument("--validate", action="store_true",
                    help="写盘前用 schemas/vault-profile.schema.json 校验")
    args = ap.parse_args(argv)

    try:
        profile = build(args.vault)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    payload = profile if args.with_meta else strip_meta(profile)

    if args.validate:
        schema = load_schema()
        if schema is None:
            print("[validate] 找不到 schemas/vault-profile.schema.json，跳过。", file=sys.stderr)
        else:
            # 校验恒针对 schema 正式字段；_scan/_evidence/_unresolved/_warnings 是台账，
            # 不在 schema 内（--with-meta 只是把它们一并输出给人工复核）。
            target = strip_meta(profile)
            try:
                import jsonschema  # type: ignore
                jsonschema.validate(target, schema)
                print("[validate] OK（jsonschema）")
            except ImportError:
                errs = minimal_validate(target, schema)
                if errs:
                    print("[validate] 失败（内置校验器）：", file=sys.stderr)
                    for e in errs:
                        print("  -", e, file=sys.stderr)
                    return 3
                print("[validate] OK（内置校验器）")
            except Exception as exc:  # jsonschema.ValidationError
                print("[validate] 失败：", exc, file=sys.stderr)
                return 3

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.do_print:
        print(text)
        return 0

    out = args.out or os.path.join(args.vault, "_scripts", "vault-profile.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(f"[build_vault_profile] 已写入 {out}")
    fm = payload.get("frontmatter", {})
    req = fm.get("required") or []
    rpt = fm.get("requiredPerNoteType") or {}
    print(f"[build_vault_profile] reviewedByHuman=false；warnings={len(profile['_warnings'])}；"
          f"未找到字段={len(profile['_unresolved'])}")
    print(f"[build_vault_profile] frontmatter.required = {len(req)} 个字段 {req}")
    print(f"[build_vault_profile] frontmatter.requiredPerNoteType = {len(rpt)} 类"
          + ("（" + "、".join(f"{k}:{len(v)}" for k, v in
                             sorted(rpt.items(), key=lambda x: -len(x[1]))[:5]) + " …）"
             if rpt else ""))
    if fm.get("requiredIsInferred") and not payload.get("vault", {}).get("reviewedByHuman"):
        print("[build_vault_profile] ⚠ 重要：`requiredIsInferred=true` 且 "
              "`reviewedByHuman=false` ——\n"
              "    frontmatter.required 是**从目录频次推断的值，不是硬要求**。\n"
              "    复核前不要据此报 ERROR；校验器应降为 WARN/INFO 并写明「画像未经人工复核」。\n"
              "    按类型的真实必填请读 `frontmatter.requiredPerNoteType`。")
    for w in profile["_warnings"]:
        print(f"  ⚠ {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
