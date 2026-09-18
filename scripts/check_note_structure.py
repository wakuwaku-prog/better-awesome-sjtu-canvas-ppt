#!/usr/bin/env python
"""Check that course notes follow the vault's note conventions.

Verifies, for every lecture/video/ppt note it is pointed at:

* YAML frontmatter present, with the required keys
* a source location (节次 / 时间码 / 页码) is referenced somewhere
* images are EMBEDDED (``![[...]]``) rather than linked (``[[...]]``)
* every table row that embeds an image escapes the pipe (``\\|``)
* a Chinese synthesis section exists
* at least N active-recall questions exist
* a "待核实" list exists and is not empty
* at least one outbound wikilink to another note exists

It is a *structure* check, not a fact check: it cannot tell whether the content is
right, only whether the note is shaped the way the vault expects.

Usage
-----
python check_note_structure.py <vault_or_dir> [--min-recall 3] [--quiet]
"""
import argparse
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
EMBED = re.compile(r"!\[\[([^\[\]]+?)\]\]")
LINK = re.compile(r"(?<!!)\[\[([^\[\]]+?)\]\]")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
REQUIRED_KEYS = ("type", "course_code", "status", "tags")
# "source" is the canonical key; "source_file" is an accepted variant in this vault.
SOURCE_KEYS = ("source", "source_file")
SYNTHESIS = re.compile(r"中文总结|Chinese synthesis|Synthesis|整体理解|小结")
RECALL_HEADING = re.compile(r"^#{2,4}\s*.*(主动回忆|Active recall)", re.M | re.I)
NUMBERED = re.compile(r"^\s*\d+\.\s+\S", re.M)

NOTE_PREFIX = ("L", "V", "P")
# Vault convention: notes are named "L## — 中文主题 English Topic" (space em-dash space).
# Transcripts and OCR dumps also start with "L0X" but have no em-dash separator, so
# requiring the separator keeps this checker off the evidence layer.
NOTE_NAME = re.compile(r"^[LVP]\d{2}\s+—\s+")
SKIP_DIRS = (".git", ".obsidian", ".trash")
# Anything under the evidence layer is raw material, not a note.
SKIP_PATH_PARTS = ("09 参考资料/附件", "09 参考资料\\附件")


def is_note(name, path=""):
    if not name.endswith(".md"):
        return False
    if any(part in path for part in SKIP_PATH_PARTS):
        return False
    return bool(NOTE_NAME.match(os.path.splitext(name)[0]))


def check(path, text, min_recall):
    problems = []
    rel = os.path.basename(path)

    fm = FRONTMATTER.match(text)
    if not fm:
        problems.append("缺少 YAML frontmatter")
        keys = set()
        note_type = ""
    else:
        keys = set(re.findall(r"^([A-Za-z_][\w-]*)\s*:", fm.group(1), re.M))
        missing = [k for k in REQUIRED_KEYS if k not in keys]
        if not any(k in keys for k in SOURCE_KEYS):
            missing.append("source")
        if missing:
            problems.append("frontmatter 缺字段: " + ", ".join(missing))
        m = re.search(r"^type:\s*(\S+)", fm.group(1), re.M)
        note_type = m.group(1) if m else ""

    # A live-capture page is a working file: it is integrated into a formal note
    # later, so the finished-note requirements do not apply.
    if note_type == "lecture-capture":
        return [], len(EMBED.findall(text)), 0, True

    if not re.search(r"\d{1,2}:\d{2}", text) and "第" not in text:
        problems.append("看不到节次或时间码（来源位置）")

    embeds = EMBED.findall(text)
    links = LINK.findall(text)
    image_links = [l for l in links
                   if l.split("|")[0].split("#")[0].strip().lower().endswith(IMAGE_EXT)]
    if image_links:
        problems.append("有 %d 处图片只放链接未内嵌（应写成 ![[...]]）" % len(image_links))

    # Tables that embed images must escape the pipe, otherwise the table breaks.
    bad_rows = 0
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        if "![[" in line and "\\|" not in line:
            bad_rows += 1
    if bad_rows:
        problems.append("表格内 %d 行嵌入了图片但未转义竖线（应写成 \\|300）" % bad_rows)

    # A ppt gallery is a page index: no synthesis section and no recall questions
    # of its own — those live in the L## note.
    if note_type != "ppt-note":
        if not SYNTHESIS.search(text):
            problems.append("缺少中文总结小节")

    # Count recall questions under the recall heading, accepting both the
    # "**定义 Define：**" style and a plain numbered question list.
    recall = 0
    head = RECALL_HEADING.search(text)
    if head:
        section = text[head.end():]
        nxt = re.search(r"^#{2,4}\s", section, re.M)
        if nxt:
            section = section[:nxt.start()]
        recall = len(NUMBERED.findall(section))
    if note_type != "ppt-note" and recall < min_recall:
        problems.append("主动回忆题只有 %d 道（要求 ≥%d）" % (recall, min_recall))

    if not re.search(r"待核实|To verify", text):
        problems.append("缺少「待核实」清单")
    elif not re.search(r"^\s*-\s*\[ \]", text, re.M):
        problems.append("「待核实」清单为空（应有未勾选项）")

    if not links:
        problems.append("没有任何指向其他笔记的 wikilink（孤岛页）")

    return problems, len(embeds), recall, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--min-recall", type=int, default=3)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    targets = []
    if os.path.isfile(args.root):
        targets = [args.root]
    else:
        for dirpath, dirnames, filenames in os.walk(args.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                full = os.path.join(dirpath, name)
                if is_note(name, full):
                    targets.append(os.path.join(dirpath, name))

    if not targets:
        print("没有找到 L## / V## / P## 笔记")
        return

    total_problems = 0
    skipped = 0
    for path in sorted(targets):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        problems, embeds, recall, is_capture = check(path, text, args.min_recall)
        if is_capture:
            skipped += 1
            if not args.quiet:
                print("SKIP  %s  (临时捕捉页，待整合)" % os.path.basename(path))
            continue
        total_problems += len(problems)
        if problems:
            print("FAIL  %s  (图片嵌入 %d，回忆题 %d)" % (os.path.basename(path), embeds, recall))
            for p in problems:
                print("        - " + p)
        elif not args.quiet:
            print("OK    %s  (图片嵌入 %d，回忆题 %d)" % (os.path.basename(path), embeds, recall))

    print("\n检查 %d 篇笔记，%d 个问题%s"
          % (len(targets), total_problems,
             "（另有 %d 篇临时捕捉页已跳过）" % skipped if skipped else ""))


if __name__ == "__main__":
    main()
