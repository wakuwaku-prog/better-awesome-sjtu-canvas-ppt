#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_note_frontmatter.py — 画像驱动的 frontmatter 必填字段检查。

**为什么需要这个脚本**
`better-awesome-sjtu-canvas-ppt/scripts/check_note_structure.py` 把必填字段名
硬编码成 `type`。细胞生物学和组织学的 AGENTS.md 规定用 `note_type`，于是该库
**全部笔记**被误报「frontmatter 缺字段: type」——红灯一被忽略，整套质量门就失效。

所以本 skill 自己做一次**画像驱动**的检查：字段名与必填清单都来自
`vault-profile.json`，而不是写死。复用校验器的同类发现由 `check_all.py`
复核后降级为 INFO。

检查项
------
1. 有 YAML frontmatter 块（`---` 开头）
2. **必填字段**，两个来源、严重度不同：
   - `frontmatter.requiredPerNoteType[<note_type 值>]` —— 按类型、粒度正确 → ERROR
   - `frontmatter.required` —— 若是目录频次**推断**的（`requiredIsInferred=true`）
     且画像**未人工复核**，缺它只报 WARN（「推断出来的要求不是要求」）
3. **`frontmatter.perNoteType` 不是必填清单**，不得据此报缺字段。
   它是**信息性**的「该类型出现过的可选字段并集」，只在输出里列出来供核对。
   （实测撞车一次：把它当必填会让 9 篇 L 笔记各被报缺 7–9 个字段。）
4. 结构要求（总结小节 / 回忆题 / 待核实清单），阈值全部来自画像 `structure`
5. 记录实际的 note_type 取值分布，便于核对画像的两个 perNoteType 键是否过时

用法
----
python check_note_frontmatter.py --profile <vault-profile.json> [--vault DIR]
                                 [--min-recall-unused] [--quiet] [--json]

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

# 与复用校验器一致的笔记识别规则：L##/V##/P## + 空格 em-dash 空格
NOTE_NAME = re.compile(r"^[LVP]\d{2}\s+—\s+")
SKIP_DIRS = (".git", ".obsidian", ".trash")
# 证据层是原始材料，不是笔记
SKIP_PATH_PARTS = ("09 参考资料/附件", "09 参考资料\\附件")
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)

# note_type / type 的候选字段名（不同库叫法不同，画像里会写清楚）
TYPE_KEYS = ("note_type", "type")

# ---- 结构检查用的模式（与复用校验器的判据等价，但阈值来自画像）----
RECALL_HEADING = re.compile(r"^#{2,4}\s*.*(主动回忆|Active recall)", re.M | re.I)
# 题号：`1.` / `1、` / `A.` / `A、`（复用校验器只认数字编号，会把 A./B. 的子题算成 0）
NUMBERED = re.compile(r"^\s*(?:\d+|[A-Z])[\.\、]\s+\S", re.M)
TO_VERIFY = re.compile(r"待核实|To verify")
UNCHECKED_ITEM = re.compile(r"^\s*-\s*\[ \]", re.M)
# 只在**同级或更高级**标题处截断（`## `）。若用 `#{1,6}`，`### A. …` 子标题
# 会把回忆小节提前切断 —— L08 的题号在 `### B.` 之下，实测因此数成 0 道。
SECTION_BREAK = re.compile(r"^#{1,2}\s", re.M)

# 画像 structure 段缺失时的兜底（与复用校验器的历史行为等价，可被画像覆盖）
DEFAULT_STRUCTURE = {
    "summarySectionAliases": ["中文总结", "Chinese synthesis", "整体理解", "小结",
                              "Synthesis"],
    # 总结小节判定方式（与 schema 的 summaryDetection 枚举一致）：
    #   heading-alias         = 标题命中 summarySectionAliases
    #   any-numbered-heading  = 只要存在编号标题（`## 一、…`）就算
    #   not-required          = 不要求
    "summaryDetection": "heading-alias",
    "recallQuestions": {"L": 3, "P": 0, "V": 0, "Concept": 2},
    "requireToVerifyList": {"L": True, "P": False, "V": False, "Concept": True},
    "requireUncheckedToVerifyItem": {"L": True, "P": False, "V": False,
                                     "Concept": False},
    "demoteReusedFindings": ["summary", "recall", "to-verify", "frontmatter"],
}

# 编号标题：`## 一、…` / `## 1 …` / `## 3. …`（中文数字与阿拉伯数字都算）
NUMBERED_HEADING = re.compile(
    r"^#{1,6}\s*(?:[一二三四五六七八九十百]+|\d+)\s*[、\.．,，:：\s]", re.M)


class Report(object):
    def __init__(self):
        self.findings = []
        self.checked = 0
        # 每条 finding 归属的笔记（结构化字段）——调用方据此复核，
        # 不要去解析 message 文本（截断会误归属，实测踩过）
        self.notes = []

    def add(self, level, rule, message, note=None):
        self.findings.append((level, rule, message))
        self.notes.append(note)

    def error(self, rule, message, note=None):
        self.add("ERROR", rule, message, note)

    def warn(self, rule, message, note=None):
        self.add("WARN", rule, message, note)

    def info(self, rule, message, note=None):
        self.add("INFO", rule, message, note)

    def count(self, level):
        return sum(1 for f in self.findings if f[0] == level)

    @property
    def ok(self):
        return self.count("ERROR") == 0

    def render(self, title):
        lines = ["=" * 72, "check_note_frontmatter.py — %s" % title, "=" * 72,
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


# ---------------------------------------------------------------- YAML

def parse_frontmatter(text):
    """返回 (fields_dict, raw_block) 或 (None, None)。

    优先用 PyYAML；没有 PyYAML 时退化为「键存在性 + 空值」的正则解析，
    保证在最小环境里也能跑（不因缺依赖而假装通过）。
    """
    m = FRONTMATTER.match(text)
    if not m:
        return None, None
    raw = m.group(1)
    try:
        import yaml
    except ImportError:
        return _parse_simple(raw), raw
    try:
        data = yaml.safe_load(raw)
    except Exception:
        # YAML 坏了也算「拿不到字段」，交给调用方报错
        return _parse_simple(raw), raw
    if not isinstance(data, dict):
        return {}, raw
    return data, raw


def _parse_simple(raw):
    """无 PyYAML 时的退化解析：只取顶层 `key:`，识别空值。"""
    fields = {}
    for line in raw.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def is_empty_value(v):
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


def note_kind(path, ntype):
    """判定结构阈值用哪个键。

    优先用画像 frontmatter 里声明的类型字段值（如 `lecture` / `ppt-note`）；
    取不到就退到编号前缀（L## / P## / V##），Concept 页面按文件名判定。
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r"^([LPV])\d{2}", stem)
    prefix = m.group(1) if m else None
    if stem.startswith("Concept"):
        return "Concept"
    if prefix:
        return prefix
    return ntype or "?"


def count_recall_questions(text):
    """数「主动回忆」小节里的编号/字母题。

    小节范围到**下一个 `## ` 为止**（含 `###` 子标题下的题）。
    """
    head = RECALL_HEADING.search(text)
    if not head:
        return 0, False
    section = text[head.end():]
    nxt = SECTION_BREAK.search(section)
    if nxt:
        section = section[:nxt.start()]
    return len(NUMBERED.findall(section)), True


def has_summary_section(text, aliases, detection="heading-alias"):
    """按 summaryDetection 判定是否有「总结」小节。

    heading-alias        = 标题命中 summarySectionAliases
    any-numbered-heading = 存在编号标题即算（心理库用 `## 一、…` 编号小节）
    not-required         = 不要求（直接算通过）
    """
    if detection == "not-required":
        return True, None
    if detection == "any-numbered-heading":
        m = NUMBERED_HEADING.search(text)
        if m:
            return True, m.group(0).strip()
        return False, None
    for h in re.findall(r"^#{1,6}\s*(.+)$", text, re.M):
        for a in aliases:
            if a and a in h:
                return True, h.strip()
    return False, None


def check_structure(path, text, rep, kind, struct):
    """画像驱动的结构检查。返回 dict（供 check_all 复核复用校验器的发现）。"""
    rel = os.path.relpath(path, rep_root_holder["root"]).replace("\\", "/")
    result = {"rel": rel, "kind": kind,
              "summary": None, "recall": None, "to_verify": None,
              "unchecked": None, "findings": []}

    # ① 总结小节（判定方式与是否需要由画像决定）
    aliases = struct.get("summarySectionAliases") or []
    detection = struct.get("summaryDetection") or "heading-alias"
    need_summary = (struct.get("requireSummarySection") or {}).get(kind)
    if need_summary is None:
        # schema 用 summaryDetection；`not-required` 等价于不要求
        need_summary = (detection != "not-required") and kind != "P"
    ok, heading = has_summary_section(text, aliases, detection)
    result["summary"] = ok
    if need_summary:
        rep.checked += 1
        if not ok:
            result["findings"].append("summary")
            rep.error("structure.summarySection",
                      "%s 缺总结小节（判定方式 %s；别名：%s）"
                      % (rel, detection, "、".join(aliases)), note=rel)
    else:
        rep.info("structure.summaryNotRequired",
                 "%s（%s 类）不要求总结小节，跳过" % (rel, kind))

    # ② 主动回忆题数量
    need = (struct.get("recallQuestions") or {}).get(kind)
    if need is not None:
        n, has_section = count_recall_questions(text)
        result["recall"] = n
        rep.checked += 1
        if n < need:
            result["findings"].append("recall")
            rep.error("structure.recallQuestions",
                      "%s 主动回忆题 %d 道，画像要求 %s 类 ≥%d 道"
                      % (rel, n, kind, need), note=rel)

    # ③ 「待核实」清单
    if (struct.get("requireToVerifyList") or {}).get(kind):
        present = bool(TO_VERIFY.search(text))
        result["to_verify"] = present
        rep.checked += 1
        if not present:
            result["findings"].append("toVerify")
            rep.error("structure.toVerifyList",
                      "%s 缺「待核实」清单（画像要求 %s 类必须有）" % (rel, kind),
                      note=rel)
        else:
            need_unchecked = (struct.get("requireUncheckedToVerifyItem")
                              or {}).get(kind)
            if need_unchecked:
                has_unchecked = bool(UNCHECKED_ITEM.search(text))
                result["unchecked"] = has_unchecked
                rep.checked += 1
                if not has_unchecked:
                    result["findings"].append("uncheckedItem")
                    rep.error("structure.uncheckedToVerify",
                              "%s 的「待核实」清单没有未勾选 `- [ ]` 项"
                              "（画像要求 %s 类至少一项）" % (rel, kind), note=rel)
    return result


# 供 check_structure 取库根（避免层层传参）
rep_root_holder = {"root": ""}


def note_type_of(fields, profile):
    """按画像给的字段名取 note_type 值；取不到再依次试 type/note_type。"""
    keys = []
    fm = (profile.get("frontmatter") or {})
    if isinstance(fm, dict):
        for k in ("typeKey", "noteTypeKey", "typeField"):
            if isinstance(fm.get(k), str):
                keys.append(fm[k])
    for k in TYPE_KEYS:
        if k not in keys:
            keys.append(k)
    for k in keys:
        v = fields.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip(), k
    return None, None


# ---------------------------------------------------------------- 扫描

def find_notes(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if not name.endswith(".md"):
                continue
            if any(p in full for p in SKIP_PATH_PARTS):
                continue
            if NOTE_NAME.match(os.path.splitext(name)[0]):
                out.append(full)
    return sorted(out)


def check_note(path, text, profile, rep, required_global, per_type,
               per_note_type=None, global_is_soft=False):
    """per_note_type：按 note_type 取值的必填集（粒度正确，**恒为 ERROR**）。
    global_is_soft：画像 required 是推断的且未复核 → 缺它只报 WARN。
    """
    per_note_type = per_note_type or {}
    rel = os.path.relpath(path, profile["_root"]).replace("\\", "/")
    fields, raw = parse_frontmatter(text)
    rep.checked += 1
    if fields is None:
        rep.error("frontmatter.missing",
                  "%s 缺 YAML frontmatter 块（`---` 开头）" % rel, note=rel)
        return None

    ntype, type_key = note_type_of(fields, profile)

    # 两类必填集分开算，严重度不同：
    #   type_req  —— requiredPerNoteType[<note_type>]：按类型、粒度正确 → ERROR
    #   global_req—— required：可能是目录频次推断的 → 推断且未复核时 WARN
    #
    # **绝不用 perNoteType 做必填判定**：它是信息性的「该类型出现过的可选字段
    # 并集」。实测撞车一次 —— perNoteType[lecture-note] 有 12 个字段的并集，
    # 而 requiredPerNoteType[lecture-note] 只有 4 个；当必填用会让 9 篇 L 笔记
    # 各被报缺 7–9 个字段，每篇都满足不了。那是「把并集当必填」。
    type_req = list(per_note_type.get(ntype) or []) if ntype else []
    global_req = [k for k in required_global if k not in type_req]

    # perNoteType 只作信息展示，**不参与判定**
    seen_fields = sorted(set(per_type.get(ntype) or [])) if ntype else []
    if seen_fields:
        rep.info("frontmatter.perNoteTypeInfo",
                 "%s（%s）该类型出现过的字段（**信息性，不代表必填**）：%s"
                 % (rel, ntype, ", ".join(seen_fields)), note=rel)

    missing_type = [k for k in type_req if k not in fields]
    missing_global = [k for k in global_req if k not in fields]
    missing = sorted(set(missing_type) | set(missing_global))

    rep.checked += 1
    if missing_type:
        rep.error("frontmatter.requiredPerNoteType",
                  "%s 缺该类型的必填字段 %s（note_type=%s，来自画像 "
                  "frontmatter.requiredPerNoteType）"
                  % (rel, ", ".join(missing_type), ntype or "?"), note=rel)
    if missing_global:
        rep.checked += 1
        if global_is_soft:
            # 「推断出来的要求不是要求」：长期假红灯会被忽略，整套质量门随之失效
            rep.warn("frontmatter.requiredInferred",
                     "%s 缺字段 %s —— 画像 required 是从目录频次推断的"
                     "（requiredIsInferred=true）且未经人工复核，按 WARN 处理，"
                     "**复核画像后才应视为硬要求**"
                     % (rel, ", ".join(missing_global)), note=rel)
        else:
            rep.error("frontmatter.required",
                      "%s 缺必填字段 %s（note_type=%s，字段名由画像 "
                      "frontmatter.required 给出）"
                      % (rel, ", ".join(missing_global), ntype or "?"), note=rel)

    required = sorted(set(type_req) | set(global_req))
    empty = [k for k in required if k in fields and is_empty_value(fields[k])]
    rep.checked += 1
    if empty:
        # 字段存在但为空：与「缺失」同一严重度口径
        if global_is_soft and all(k in global_req for k in empty):
            rep.warn("frontmatter.emptyInferred",
                     "%s 的推断必填字段为空：%s（未复核，按 WARN）"
                     % (rel, ", ".join(empty)), note=rel)
        else:
            rep.error("frontmatter.empty",
                      "%s 的必填字段为空：%s" % (rel, ", ".join(empty)), note=rel)

    # 实际使用的 type 字段名，便于核对画像没写错
    if type_key is None:
        rep.warn("frontmatter.typeKey",
                 "%s 找不到 note_type/type 字段，无法按类型套用 perNoteType" % rel,
                 note=rel)
    return {"rel": rel, "note_type": ntype, "type_key": type_key,
            "fields": sorted(fields), "missing": missing,
            "missingType": missing_type, "missingGlobal": missing_global,
            "empty": empty, "globalSoft": global_is_soft}


def run(profile_path, vault_override=None):
    rep = Report()
    if not os.path.isfile(profile_path):
        rep.error("profile.missing", "找不到画像：%s" % profile_path)
        return rep, {}
    try:
        with open(profile_path, encoding="utf-8") as fh:
            profile = json.load(fh)
    except json.JSONDecodeError as exc:
        rep.error("profile.badJson", "画像不是合法 JSON：%s" % exc)
        return rep, {}
    except OSError as exc:
        rep.error("profile.unreadable", "读不了画像：%s" % exc)
        return rep, {}

    root = vault_override or (profile.get("vault") or {}).get("root")
    rep.checked += 1
    if not isinstance(root, str) or not root.strip():
        rep.error("profile.root", "画像没有可用的 vault.root，且未给 --vault")
        return rep, {}
    if not os.path.isdir(root):
        rep.error("profile.rootMissing", "vault root 不是目录：%s" % root)
        return rep, {}

    profile = dict(profile)
    profile["_root"] = root

    fm = profile.get("frontmatter") or {}
    required_global = [k for k in (fm.get("required") or []) if isinstance(k, str)]
    per_type = {}
    for k, v in (fm.get("perNoteType") or {}).items():
        if isinstance(v, list):
            per_type[k] = [x for x in v if isinstance(x, str)]
    per_note_type = {}
    for k, v in (fm.get("requiredPerNoteType") or {}).items():
        if isinstance(v, list):
            per_note_type[k] = [x for x in v if isinstance(x, str)]

    # ---- 「推断出来的要求不是要求」----
    # required 若是从目录频次推断的，且画像未经人工复核，则缺字段只报 WARN：
    # 长期存在的假红灯会被忽略，整套质量门随之失效。
    # requiredPerNoteType 是按类型、粒度正确的必填集 → **不受复核状态影响，恒为 ERROR**。
    reviewed = (profile.get("vault") or {}).get("reviewedByHuman")
    inferred = fm.get("requiredIsInferred")
    global_is_soft = bool(inferred is True and reviewed is not True)
    if global_is_soft:
        rep.warn("frontmatter.requiredIsInferred",
                 "画像 frontmatter.required 是从目录频次**推断**的"
                 "（requiredIsInferred=true）且未经人工复核（reviewedByHuman=%r）；"
                 "以下缺字段按 **WARN** 处理，**复核画像后才应视为硬要求**。"
                 "按类型的 requiredPerNoteType 不受影响，仍报 ERROR。"
                 % (reviewed,))
    elif inferred is True and reviewed is True:
        rep.info("frontmatter.requiredIsInferred",
                 "required 是推断的，但画像已人工复核 → 按硬要求报 ERROR")

    # ---- 画像 structure 段（缺失时用兜底，并明确提示）----
    struct = profile.get("structure")
    rep.checked += 1
    if not isinstance(struct, dict) or not struct:
        struct = DEFAULT_STRUCTURE
        rep.warn("structure.profileMissing",
                 "画像缺 structure 段 —— 结构检查用的是内置兜底阈值"
                 "（summarySectionAliases / recallQuestions / requireToVerifyList / "
                 "requireUncheckedToVerifyItem）。请在画像里写明本库的结构要求")
    else:
        merged = dict(DEFAULT_STRUCTURE)
        merged.update({k: v for k, v in struct.items() if v is not None})
        struct = merged
    rep.info("structure.config",
             "结构阈值：总结小节判定 %s；回忆题 %s；待核实清单 %s；未勾选项 %s；别名 %s"
             % (struct.get("summaryDetection"), struct.get("recallQuestions"),
                struct.get("requireToVerifyList"),
                struct.get("requireUncheckedToVerifyItem"),
                "、".join(struct.get("summarySectionAliases") or [])))
    rep_root_holder["root"] = root

    rep.checked += 1
    if not required_global:
        rep.warn("frontmatter.requiredUnset",
                 "画像 frontmatter.required 为空 —— 无法做画像驱动的必填字段检查；"
                 "请先在画像里写明本库的必填字段名")
        return rep, {}

    rep.info("frontmatter.required",
             "全局必填字段（frontmatter.required）：%s；"
             "按类型必填字段（frontmatter.requiredPerNoteType）：%d 个类型"
             % (", ".join(required_global), len(per_note_type)))
    if per_type:
        rep.info("frontmatter.perNoteTypeInfo",
                 "frontmatter.perNoteType 覆盖 %d 个类型，"
                 "**仅作信息展示，不代表必填**" % len(per_type))

    notes = find_notes(root)
    rep.info("frontmatter.notes", "扫描到 %d 篇 L##/V##/P## 笔记" % len(notes))
    if not notes:
        rep.warn("frontmatter.noNotes",
                 "库里没有找到 L##/V##/P## 笔记 —— 确认库路径是否正确：%s" % root)
        return rep, {}

    results = []
    for path in notes:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            rep.error("frontmatter.unreadable", "%s 读不了：%s" % (path, exc))
            continue
        r = check_note(path, text, profile, rep, required_global, per_type,
                       per_note_type=per_note_type, global_is_soft=global_is_soft)
        if r:
            # 结构检查（画像驱动）—— 同一遍扫描里做完
            kind = note_kind(path, r.get("note_type"))
            r["kind"] = kind
            r["structure"] = check_structure(path, text, rep, kind, struct)
            results.append(r)

    # note_type 取值分布：核对两个 perNoteType 键是否过时
    dist = {}
    for r in results:
        if r["note_type"]:
            dist[r["note_type"]] = dist.get(r["note_type"], 0) + 1
    if dist:
        rep.info("frontmatter.typeDistribution",
                 "note_type 取值分布：%s"
                 % ", ".join("%s×%d" % (k, v) for k, v in sorted(dist.items())))
        unknown = [k for k in dist if per_type and k not in per_type]
        if per_type and unknown:
            rep.info("frontmatter.perNoteTypeCoverage",
                     "以下 note_type 未在 perNoteType 里出现（只用全局必填清单）：%s"
                     % ", ".join(sorted(unknown)))
    # 结构结果汇总：哪些笔记在哪一项上通过（供 check_all 复核复用校验器的发现）
    by_kind = {}
    for r in results:
        by_kind[r.get("kind")] = by_kind.get(r.get("kind"), 0) + 1
    if by_kind:
        rep.info("structure.kinds", "笔记类型分布（结构阈值按此分）：%s"
                 % ", ".join("%s×%d" % (k, v) for k, v in sorted(by_kind.items())))
    return rep, {"required": required_global, "per_type": per_type,
                 "structure": struct, "notes": results,
                 "demote": struct.get("demoteReusedFindings") or []}


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="check_note_frontmatter.py",
        description="画像驱动的 frontmatter 必填字段检查（替代复用校验器硬编码 type 的误报）")
    ap.add_argument("--profile", required=True, help="vault-profile.json 路径")
    ap.add_argument("--vault", default=None, help="覆盖画像里的 vault.root")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rep, info = run(args.profile, args.vault)
    _ = info if isinstance(info, dict) else {}

    if args.json:
        print(json.dumps({
            "profile": args.profile, "ok": rep.ok, "checked": rep.checked,
            "errors": rep.count("ERROR"), "warns": rep.count("WARN"),
            "structure": (_["structure"] if isinstance(_, dict) else None),
            "demoteReusedFindings": (_["demote"] if isinstance(_, dict) else None),
            "findings": [{"level": l, "rule": r, "message": m, "note": n}
                         for (l, r, m), n in zip(rep.findings, rep.notes)],
        }, ensure_ascii=False, indent=2))
    elif args.quiet:
        print("RESULT: %s (ERROR %d, WARN %d)"
              % ("PASS" if rep.ok else "FAIL", rep.count("ERROR"), rep.count("WARN")))
    else:
        print(rep.render(os.path.basename(args.profile)))

    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
