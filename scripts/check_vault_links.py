"""Validate Obsidian wikilinks and embeds against the real files in the vault.

Handles ``[[path|alias]]``, ``[[path\\|alias]]`` (escaped pipes inside tables),
``![[image.png|300]]`` and heading/block suffixes (``#...``).

Usage: python check_links.py <vault_root>
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

VAULT = sys.argv[1]
LINK = re.compile(r"(!?)\[\[([^\[\]]+?)\]\]")

# basename (without extension) -> list of relative paths
by_stem = {}
by_name = {}
for root, dirs, files in os.walk(VAULT):
    dirs[:] = [d for d in dirs if d not in (".git", ".obsidian", ".trash")]
    for name in files:
        full = os.path.join(root, name)
        rel = os.path.relpath(full, VAULT).replace("\\", "/")
        by_name[rel] = True
        stem = os.path.splitext(name)[0]
        by_stem.setdefault(stem, []).append(rel)

problems = []
checked = 0
for root, dirs, files in os.walk(VAULT):
    dirs[:] = [d for d in dirs if d not in (".git", ".obsidian", ".trash")]
    for name in files:
        if not name.endswith(".md"):
            continue
        rel = os.path.relpath(os.path.join(root, name), VAULT).replace("\\", "/")
        with open(os.path.join(root, name), encoding="utf-8") as fh:
            text = fh.read()
        for bang, target in LINK.findall(text):
            checked += 1
            raw = target.replace("\\|", "|")
            path = raw.split("|")[0].split("#")[0].strip()
            if not path:
                continue
            if path in by_name:
                continue
            stem = os.path.splitext(os.path.basename(path))[0]
            if stem in by_stem:
                continue
            problems.append((rel, bang + "[[" + target + "]]"))

print("checked %d links/embeds in the vault" % checked)
if not problems:
    print("OK: every wikilink and embed resolves to a real file")
else:
    print("UNRESOLVED (%d):" % len(problems))
    for rel, link in problems:
        print("  %s  ->  %s" % (rel, link))
