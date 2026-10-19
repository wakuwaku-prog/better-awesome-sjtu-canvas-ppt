#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_vault_profile.py — 只读校验知识库画像 vault-profile.json。

画像声明的目录 / 模板 / Source Register 文件必须在目标库里**真实存在**——
画像猜错目录会让下游把笔记写到错误位置，这是最贵的一类错误。

检查项
------
1. 结构：schemaVersion=1；vault.root/name；dirs.lecture；mediaPolicy 三个布尔
2. vault.root 目录存在
3. dirs.* 每个声明的路径在库里存在（缺失时 ERROR，可用 --lenient-dirs 降为 WARN）
4. dirs.sourceRegister / dirs.templates / vault.agentsMd 指向真实文件
5. vault.reviewedByHuman=false 时给出明确警告（画像复核前只允许只读操作）
6. images.embedSyntax 与目标库嵌入语法自洽；explainer 宽度不写死 px

用法
----
python check_vault_profile.py <vault-profile.json> [--vault DIR] [--lenient-dirs]
                              [--quiet] [--json]

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

# dirs 里「指向文件」而不是目录的键
FILE_KEYS = ("sourceRegister",)
# 允许为 null 的键（该库不使用该产物）
NULLABLE_KEYS = ("deck", "timeline", "concept", "method", "disorder", "assessment",
                 "explainer", "quizBank", "sourceRegister", "templates", "scratch")
REQUIRED_DIR_KEYS = ("lecture", "attachments")


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
        lines = ["=" * 72, "check_vault_profile.py — %s" % title, "=" * 72,
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


def is_nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def check_structure(prof, rep):
    rep.checked += 1
    if prof.get("schemaVersion") != 1:
        rep.error("profile.schemaVersion",
                  "schemaVersion 必须是数字 1，实际 %r" % prof.get("schemaVersion"))
    vault = prof.get("vault")
    if not isinstance(vault, dict):
        rep.error("profile.vault", "vault 必须是对象")
        return None
    for key in ("root", "name"):
        rep.checked += 1
        if not is_nonempty_str(vault.get(key)):
            rep.error("profile.vault.%s" % key, "vault.%s 必须非空" % key)
    if "reviewedByHuman" in vault and not isinstance(vault["reviewedByHuman"], bool):
        rep.error("profile.vault.reviewedByHuman",
                  "reviewedByHuman 必须是布尔值")

    dirs = prof.get("dirs")
    if not isinstance(dirs, dict):
        rep.error("profile.dirs", "dirs 必须是对象")
    else:
        for key in REQUIRED_DIR_KEYS:
            rep.checked += 1
            if key not in dirs:
                rep.error("profile.dirs.%s" % key, "dirs.%s 是必填字段" % key)
            elif not is_nonempty_str(dirs.get(key)):
                rep.error("profile.dirs.%s" % key,
                          "dirs.%s 必须是非空字符串（相对知识库根）" % key)
        for key, val in dirs.items():
            rep.checked += 1
            if key == "excluded":
                # schema: array of strings（不进入共享范围的目录）
                if not isinstance(val, list):
                    rep.error("profile.dirs.excluded",
                              "dirs.excluded 必须是字符串数组，实际 %r" % type(val).__name__)
                else:
                    for item in val:
                        if not is_nonempty_str(item):
                            rep.error("profile.dirs.excluded",
                                      "dirs.excluded 里有非字符串项：%r" % (item,))
                continue
            if val is None:
                if key not in NULLABLE_KEYS:
                    rep.error("profile.dirs.null",
                              "dirs.%s 为 null，但该键不允许 null" % key)
            elif not is_nonempty_str(val):
                rep.error("profile.dirs.type",
                          "dirs.%s 必须是字符串或 null，实际 %r" % (key, val))

    mp = prof.get("mediaPolicy")
    if not isinstance(mp, dict):
        rep.error("profile.mediaPolicy", "mediaPolicy 必须是对象")
    else:
        for key in ("archiveVideo", "archiveAudio", "archiveTranscript"):
            rep.checked += 1
            if not isinstance(mp.get(key), bool):
                rep.error("profile.mediaPolicy.%s" % key,
                          "mediaPolicy.%s 必须是布尔值" % key)
    return vault


def check_paths(prof, vault, vault_override, rep, lenient_dirs):
    root = vault_override or vault.get("root")
    if not is_nonempty_str(root):
        rep.error("profile.root", "没有可用的 vault root")
        return
    rep.checked += 1
    if not os.path.isdir(root):
        rep.error("profile.rootMissing", "vault.root 目录不存在：%s" % root)
        return
    rep.info("profile.root", "vault root = %s" % root)

    dirs = prof.get("dirs") or {}
    for key, val in sorted(dirs.items()):
        if not is_nonempty_str(val):
            continue
        rep.checked += 1
        full = os.path.join(root, val.replace("/", os.sep))
        exists = os.path.exists(full)
        if not exists:
            level_fn = rep.warn if lenient_dirs else rep.error
            level_fn("profile.dirMissing",
                     "dirs.%s 声明 %r，但库里不存在：%s" % (key, val, full))
        elif key in FILE_KEYS and not os.path.isfile(full):
            rep.error("profile.dirNotFile",
                      "dirs.%s 声明 %r，但那是目录不是文件" % (key, val))
        else:
            kind = "文件" if os.path.isfile(full) else "目录"
            rep.info("profile.dirOk", "dirs.%s → %s（%s）" % (key, val, kind))

    # templates 可能是目录，指向真实存在即可
    tmpl = dirs.get("templates")
    if is_nonempty_str(tmpl):
        full = os.path.join(root, tmpl.replace("/", os.sep))
        rep.checked += 1
        if os.path.isdir(full):
            n = len([f for f in os.listdir(full) if f.endswith(".md")])
            rep.info("profile.templates", "templates 目录 %r 内有 %d 个 .md" % (tmpl, n))
            if n == 0:
                rep.warn("profile.templatesEmpty",
                         "templates 目录 %r 里没有 .md 模板" % tmpl)

    agents = vault.get("agentsMd")
    if is_nonempty_str(agents):
        full = os.path.join(root, agents.replace("/", os.sep))
        rep.checked += 1
        if not os.path.isfile(full):
            rep.error("profile.agentsMdMissing",
                      "vault.agentsMd 声明 %r，但库里没有这个文件" % agents)
        else:
            rep.info("profile.agentsMd", "AGENTS.md 存在：%s" % agents)
    else:
        rep.warn("profile.agentsMd",
                 "画像没记 agentsMd —— 本 skill 的第一读是目标库 AGENTS.md，"
                 "缺了会拿默认值兜底")


def check_review(prof, vault, rep):
    reviewed = vault.get("reviewedByHuman")
    rep.checked += 1
    if reviewed is False:
        rep.warn("profile.notReviewed",
                 "reviewedByHuman=false —— 画像未经人工复核，"
                 "此时**只允许只读操作**；写笔记前必须人工确认目录映射")
    elif reviewed is True:
        rep.info("profile.reviewed", "reviewedByHuman=true（已人工复核）")
    else:
        rep.warn("profile.notReviewed",
                 "画像缺 reviewedByHuman 字段 —— 视为未复核，只允许只读操作")


def check_rendering(prof, rep):
    images = prof.get("images") or {}
    syntax = images.get("embedSyntax")
    if is_nonempty_str(syntax):
        rep.checked += 1
        if "[[" not in syntax or "]]" not in syntax:
            rep.error("profile.embedSyntax",
                      "images.embedSyntax=%r 不像 Obsidian wikilink 模板" % syntax)
        elif "|" not in syntax and "{width}" in syntax:
            rep.warn("profile.embedSyntax",
                     "embedSyntax 带 {width} 但没有竖线分隔：%r" % syntax)
        else:
            rep.info("profile.embedSyntax", "embedSyntax = %r" % syntax)
    if images.get("escapePipeInTables") is False:
        rep.error("profile.escapePipe",
                  "images.escapePipeInTables 必须为 true（表格内嵌图片要写 \\|）")

    ex = prof.get("explainer") or {}
    wa = ex.get("imageWidthAttr")
    if is_nonempty_str(wa):
        rep.checked += 1
        if re.search(r"(?<!min\()\bmax-width:\s*\d+px", wa) or \
           re.search(r"(?<!min\()\bwidth:\s*\d+px", wa):
            rep.error("profile.imageWidthAttr",
                      "explainer.imageWidthAttr=%r 写死了 px 宽度"
                      "（窄屏会顶破卡片，应用 max-width:min(Npx,100%%)）" % wa)

    quiz = prof.get("quiz") or {}
    for key in ("correctOptionMustNotBeLongest", "shuffleAnswerPositions",
                "explanationsCiteSource"):
        if key in quiz and quiz[key] is not True:
            rep.error("profile.quiz.%s" % key,
                      "quiz.%s 是不可关闭的硬规则，必须为 true" % key)


def run(profile_path, vault_override=None, lenient_dirs=False):
    rep = Report()
    if not os.path.isfile(profile_path):
        rep.error("profile.missing", "找不到画像文件：%s" % profile_path)
        return rep, None
    try:
        with open(profile_path, encoding="utf-8") as fh:
            prof = json.load(fh)
    except json.JSONDecodeError as exc:
        rep.error("profile.badJson", "不是合法 JSON：%s" % exc)
        return rep, None
    except OSError as exc:
        rep.error("profile.unreadable", "读不了：%s" % exc)
        return rep, None

    if not isinstance(prof, dict):
        rep.error("profile.type", "画像顶层必须是对象")
        return rep, prof

    vault = check_structure(prof, rep)
    if vault is not None:
        check_paths(prof, vault, vault_override, rep, lenient_dirs)
        check_review(prof, vault, rep)
    check_rendering(prof, rep)
    return rep, prof


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="check_vault_profile.py",
        description="只读校验知识库画像：声明的目录/模板/Source Register 是否真实存在")
    ap.add_argument("profile", help="vault-profile.json 路径")
    ap.add_argument("--vault", default=None, help="覆盖画像里的 vault.root")
    ap.add_argument("--lenient-dirs", action="store_true",
                    help="缺失目录降为 WARN（画像还在草稿阶段时用）")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rep, _ = run(args.profile, args.vault, args.lenient_dirs)

    if args.json:
        print(json.dumps({
            "profile": args.profile,
            "ok": rep.ok,
            "checked": rep.checked,
            "errors": rep.count("ERROR"),
            "warns": rep.count("WARN"),
            "findings": [{"level": l, "rule": r, "message": m}
                         for l, r, m in rep.findings],
        }, ensure_ascii=False, indent=2))
    elif args.quiet:
        print("RESULT: %s (ERROR %d, WARN %d)"
              % ("PASS" if rep.ok else "FAIL", rep.count("ERROR"), rep.count("WARN")))
    else:
        print(rep.render(os.path.basename(args.profile)))

    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
