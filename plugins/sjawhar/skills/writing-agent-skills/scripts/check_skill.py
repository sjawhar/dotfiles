#!/usr/bin/env python3
"""Size, description, name and link checks for agent skills (writing-agent-skills: "Limits").

Usage: check_skill.py [--old OLD] [--log FILE] <skill dir or SKILL.md>...

It prints a FAIL line and exits 1 when a skill breaks any of these:
- The description is empty or missing.
- The description does not start "Use when".
- The description has more than 300 characters.
- The name is not lowercase words joined by hyphens.
- SKILL.md or a reference has more than 150 lines. A last line with no final newline counts.
- SKILL.md or a reference has more than 8,000 bytes, frontmatter included.
- A path that SKILL.md links or names does not exist.
- With --old: SKILL.md is more than 200 bytes larger than the old copy.

A reference is each markdown file (.md in any letter case) at any depth under the skill
directory, other than the skill's own SKILL.md, that is not a template. A template is a file
whose path below the skill directory has a directory named exactly "assets" or "templates":
assets/brief.md, templates/x.md, references/templates/x.yaml. Only that path makes a template,
never the words in the file. Templates, scripts and other files that are not markdown are not
measured.

It prints a warn line and still passes when the body has more than 40 rule lines, or when the
name differs from the directory. Standard library only.
"""

import argparse
import datetime
import re
import sys
from pathlib import Path
from typing import TypedDict

# Limits: SKILL.md or a reference over TARGET_BYTES or TARGET_LINES, or a description over TARGET_DESC, fails.
TARGET_BYTES = 8_000  # Codex injects only the first 8,000 bytes
TARGET_LINES = 150
TARGET_DESC = 300
RULE_LINES = 40  # every-rule-obeyed rates collapse between 40 and 80 rules
GROWTH_ALLOWANCE = 200  # a rewrite may grow this much and no more
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\]\(([^)#\s]+)\)|`((?:references|scripts|assets)/[^`\s]+)`")
TEMPLATE_DIRS = frozenset({"assets", "templates"})

# SKILL.md's lines and bytes, its body's words, its description's characters and its rule lines.
Stats = TypedDict(
    "Stats",
    {"skill": str, "lines": int, "bytes": int, "body_words": int, "desc_chars": int, "rule_lines": int},
)


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


def references(skill_dir: Path) -> list[Path]:
    """Each markdown file under skill_dir, at any depth, except its own SKILL.md and each template."""
    found: list[Path] = []
    for path in sorted(skill_dir.rglob("*")):
        rel = path.relative_to(skill_dir)
        if (
            path.suffix.lower() == ".md"
            and rel.parts != ("SKILL.md",)
            and not TEMPLATE_DIRS.intersection(rel.parts[:-1])
            and path.is_file()
        ):
            found.append(path)
    return found


def size_fails(label: str, lines: int, size: int) -> list[str]:
    fails: list[str] = []
    if lines > TARGET_LINES:
        fails.append(f"{label} has {lines:,} lines, over the {TARGET_LINES}-line limit")
    if size > TARGET_BYTES:
        fails.append(f"{label} has {size:,} bytes, over the {TARGET_BYTES:,}-byte limit")
    return fails


def check(path: Path) -> tuple[Stats, list[str], list[str]]:
    path = path.expanduser().resolve()
    skill_md = path if path.name == "SKILL.md" else path / "SKILL.md"
    skill_dir = skill_md.parent
    raw = skill_md.read_bytes()
    text = raw.decode("utf-8")
    fields, body = split_frontmatter(text)
    desc = fields.get("description", "")
    name = fields.get("name", "")
    rule_lines = sum(1 for line in body.splitlines() if re.match(r"^\s*(?:[-*]|\d+\.)\s+\S", line))
    lines = len(text.splitlines())
    stats: Stats = {
        "skill": skill_dir.name,
        "lines": lines,
        "bytes": len(raw),
        "body_words": len(body.split()),
        "desc_chars": len(desc),
        "rule_lines": rule_lines,
    }
    fails = size_fails("SKILL.md", lines, len(raw))
    for ref in references(skill_dir):
        ref_raw = ref.read_bytes()
        ref_lines = len(ref_raw.decode("utf-8", errors="replace").splitlines())
        fails += size_fails(ref.relative_to(skill_dir).as_posix(), ref_lines, len(ref_raw))
    warns: list[str] = []
    if not desc:
        fails.append("description is empty or missing")
    else:
        if not re.match(r"Use when\b", desc):
            fails.append('description does not start "Use when"')
        if len(desc) > TARGET_DESC:
            fails.append(f"description has {len(desc):,} characters, over the {TARGET_DESC}-character limit")
    if not NAME_RE.match(name):
        fails.append(f"name {name!r} is not lowercase words joined by hyphens")
    elif name != skill_dir.name:
        warns.append(f"name {name!r} differs from directory {skill_dir.name!r}")
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


class Args(argparse.Namespace):
    paths: list[Path] = []
    log: Path | None = None
    old: Path | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _ = parser.add_argument("paths", nargs="+", type=Path)
    _ = parser.add_argument("--log", type=Path, help="append one dated TSV row per skill to this file")
    _ = parser.add_argument(
        "--old",
        type=Path,
        help=(
            "old copy of the one skill checked: its directory, its SKILL.md, or a pipe such as "
            "<(git show HEAD:path/SKILL.md); fail if SKILL.md grew too much"
        ),
    )
    args = parser.parse_args(namespace=Args())
    if args.old and len(args.paths) != 1:
        parser.error("--old takes exactly one skill path")
    old_bytes = None
    if args.old:
        old_md = args.old.expanduser()
        if old_md.is_dir():
            old_md = old_md / "SKILL.md"
        try:
            # Read the bytes rather than stat the path: a pipe from <(git show ...) has no size.
            old_bytes = len(old_md.read_bytes())
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            parser.error(
                (
                    f"--old: no SKILL.md at {args.old}; give the old skill's directory, its SKILL.md, "
                    "or a pipe such as <(git show HEAD:path/SKILL.md)"
                )
            )
    failed = False
    rows: list[Stats] = []
    for path in args.paths:
        stats, fails, warns = check(path)
        if old_bytes is not None:
            if stats["bytes"] > old_bytes + GROWTH_ALLOWANCE:
                fails.append(
                    (
                        f"SKILL.md grew from {old_bytes} to {stats['bytes']} bytes, "
                        f"over the {GROWTH_ALLOWANCE}-byte allowance"
                    )
                )
        rows.append(stats)
        print(
            (
                f"{stats['skill']}: {stats['lines']} lines, {stats['bytes']} bytes, {stats['body_words']} body words, "
                f"description {stats['desc_chars']} chars, {stats['rule_lines']} rule lines"
            )
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
                _ = fh.write("date\tskill\tbytes\tbody_words\tdesc_chars\trule_lines\n")
            today = datetime.date.today().isoformat()
            for s in rows:
                _ = fh.write(
                    f"{today}\t{s['skill']}\t{s['bytes']}\t{s['body_words']}\t{s['desc_chars']}\t{s['rule_lines']}\n"
                )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
