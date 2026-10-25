#!/usr/bin/env python3
"""Size, description and link checks for agent skills (writing-agent-skills, step D).

Usage: check_skill.py [--log FILE] <skill dir or SKILL.md>...
Exit 1 when any skill fails a hard check. Standard library only.
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

HARD_BYTES = 20_000  # Claude Code keeps about this much of a skill after compaction
TARGET_BYTES = 8_000  # Codex injects only the first 8,000 bytes
HARD_DESC = 1_024  # Codex catalog cap; also the Agent Skills spec limit
TARGET_DESC = 500
RULE_LINES = 40  # every-rule-obeyed rates collapse between 40 and 80 rules
PRECEDENCE = "say which line of this skill is wrong"
GROWTH_ALLOWANCE = 200  # a rewrite may add the precedence sentence (about 135 bytes) and no more
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\]\(([^)#\s]+)\)|`((?:references|scripts|assets)/[^`\s]+)`")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    fields: dict[str, str] = {}
    key = None
    for line in text[4:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            if value[:1] in "\"'" and value[-1:] == value[:1] and len(value) > 1:
                value = value[1:-1]
            fields[key] = "" if value in (">", "|", ">-", "|-") else value
        elif key and line.startswith((" ", "\t")):
            fields[key] = (fields[key] + " " + line.strip()).strip()
    return fields, text[end + 4 :].lstrip("\n")


def check(path: Path) -> tuple[dict, list[str], list[str]]:
    path = path.expanduser().resolve()
    skill_md = path if path.name == "SKILL.md" else path / "SKILL.md"
    skill_dir = skill_md.parent
    raw = skill_md.read_bytes()
    text = raw.decode("utf-8")
    fields, body = split_frontmatter(text)
    desc = fields.get("description", "")
    name = fields.get("name", "")
    rule_lines = sum(1 for line in body.splitlines() if re.match(r"^\s*(?:[-*]|\d+\.)\s+\S", line))
    stats = {
        "skill": skill_dir.name,
        "bytes": len(raw),
        "body_words": len(body.split()),
        "desc_chars": len(desc),
        "rule_lines": rule_lines,
    }
    fails: list[str] = []
    warns: list[str] = []
    if len(raw) > HARD_BYTES:
        fails.append(f"SKILL.md is {len(raw)} bytes, over {HARD_BYTES}")
    elif len(raw) > TARGET_BYTES:
        warns.append(f"SKILL.md is {len(raw)} bytes, over the {TARGET_BYTES} target")
    if not desc:
        fails.append("description is empty or missing")
    elif len(desc) > HARD_DESC:
        fails.append(f"description is {len(desc)} characters, over {HARD_DESC}")
    elif len(desc) > TARGET_DESC:
        warns.append(f"description is {len(desc)} characters, over the {TARGET_DESC} target")
    if desc and not desc.startswith("Use "):
        fails.append('description does not start with "Use"')
    if not NAME_RE.match(name):
        fails.append(f"name {name!r} is not lowercase words joined by hyphens")
    elif name != skill_dir.name:
        warns.append(f"name {name!r} differs from directory {skill_dir.name!r}")
    if PRECEDENCE not in " ".join(body.split()):
        fails.append("precedence sentence missing (copy it from writing-agent-skills)")
    if rule_lines > RULE_LINES:
        warns.append(f"{rule_lines} rule lines, over {RULE_LINES}: split or delete")
    # A skill inside a repository may name a repo-root path (`scripts/deploy/x.sh`) as well as its own files.
    roots = [skill_dir] + [p for p in skill_dir.parents if (p / ".git").exists()][:1]
    for m in LINK_RE.finditer(body):
        target = m.group(1) or m.group(2)
        if re.match(r"^[a-z]+://|^mailto:|^~|^/", target) or re.search(r"[<>*{}]", target):
            continue
        if not any((root / target).exists() for root in roots):
            fails.append(f"missing path: {target}")
    return stats, fails, warns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--log", type=Path, help="append one dated TSV row per skill to this file")
    parser.add_argument("--old", type=Path, help="old copy of the one skill checked; fail if SKILL.md grew too much")
    args = parser.parse_args()
    if args.old and len(args.paths) != 1:
        parser.error("--old takes exactly one skill path")
    old_md = None
    if args.old:
        old_md = args.old.expanduser()
        if old_md.is_dir():
            old_md = old_md / "SKILL.md"
        if not old_md.is_file():
            parser.error(f"--old: no SKILL.md at {args.old}")
    failed = False
    rows = []
    for path in args.paths:
        stats, fails, warns = check(path)
        if old_md:
            old_bytes = old_md.stat().st_size
            if stats["bytes"] > old_bytes + GROWTH_ALLOWANCE:
                fails.append(
                    f"SKILL.md grew from {old_bytes} to {stats['bytes']} bytes, "
                    f"over the {GROWTH_ALLOWANCE}-byte allowance"
                )
        rows.append(stats)
        print(
            f"{stats['skill']}: {stats['bytes']} bytes, {stats['body_words']} body words, "
            f"description {stats['desc_chars']} chars, {stats['rule_lines']} rule lines"
        )
        for msg in fails:
            print(f"  FAIL {msg}")
        for msg in warns:
            print(f"  warn {msg}")
        failed = failed or bool(fails)
    if args.log:
        new = not args.log.exists()
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open("a") as fh:
            if new:
                fh.write("date\tskill\tbytes\tbody_words\tdesc_chars\trule_lines\n")
            today = datetime.date.today().isoformat()
            for s in rows:
                fh.write(f"{today}\t{s['skill']}\t{s['bytes']}\t{s['body_words']}\t{s['desc_chars']}\t{s['rule_lines']}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
