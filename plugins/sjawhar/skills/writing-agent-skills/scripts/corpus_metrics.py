#!/usr/bin/env python3
"""Whole-corpus numbers for the skills and the always-loaded CLAUDE.md (writing-agent-skills, section 5).

Usage: corpus_metrics.py [--days N] [--claude-md FILE] [--no-sessions] [skills dir]
Run it before and after a batch of skill edits to see whether the corpus grew. Session numbers
count successful `read`s of `skill://<name>` in the transcript store (`tq sql`), so they miss
hosts the store does not ingest and Claude Code's Skill tool. Standard library only.
"""

import argparse
import datetime
import statistics
import subprocess
import sys
from pathlib import Path

from check_skill import RULE_LINES, TARGET_BYTES, check

SKILLS_DIR = Path(__file__).resolve().parents[2]  # plugins/sjawhar/skills
CLAUDE_MD_CAP = 8_000  # chosen cap for the one file pushed into every session; it was 6,072 bytes on 2026-10-26
SESSIONS = (
    "select session_id, uniqExact(extract(command, 'skill://([A-Za-z0-9_-]+)')) as skills "
    "from calls where tool = 'read' and match(command, 'skill://[A-Za-z0-9_-]+') "
    "and not ifNull(is_error, false) and started_at > now() - interval {days} day group by session_id"
)
COVERAGE = "select toDate(min(started_at)) from calls"


def tq(query: str) -> list[list[str]]:
    out = subprocess.run(["tq", "sql", "--format", "TSVWithNames", query], stdout=subprocess.PIPE, text=True, check=True)
    return [line.split("\t") for line in out.stdout.splitlines()[1:]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("skills_dir", nargs="?", type=Path, default=SKILLS_DIR)
    parser.add_argument("--claude-md", type=Path, default=Path.home() / ".dotfiles" / ".claude" / "CLAUDE.md")
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--no-sessions", action="store_true")
    args = parser.parse_args()

    raw = args.claude_md.expanduser().read_bytes()
    lines = raw.count(b"\n")
    print(f"always-loaded CLAUDE.md: {len(raw)} bytes of {CLAUDE_MD_CAP} cap, {lines} lines")

    stats = [check(p.parent)[0] for p in sorted(args.skills_dir.expanduser().glob("*/SKILL.md"))]
    print(
        f"skills: {len(stats)}, {sum(s['bytes'] for s in stats)} SKILL.md bytes, "
        f"{sum(s['rule_lines'] for s in stats)} rule lines"
    )
    print(f"  over {TARGET_BYTES} bytes: {', '.join(s['skill'] for s in stats if s['bytes'] > TARGET_BYTES) or 'none'}")
    print(f"  over {RULE_LINES} rule lines: {', '.join(s['skill'] for s in stats if s['rule_lines'] > RULE_LINES) or 'none'}")

    if not args.no_sessions:
        cutoff = (datetime.date.today() - datetime.timedelta(days=args.days)).isoformat()
        first = tq(COVERAGE)[0][0]
        if first > cutoff:
            print(f"# warning: the store starts on {first}, so it covers less than {args.days} days", file=sys.stderr)
        counts = [int(row[1]) for row in tq(SESSIONS.format(days=args.days))]
        if counts:
            over = sum(c > 3 for c in counts)
            print(
                f"sessions that loaded a skill, last {args.days} days: {len(counts)}; "
                f"median {statistics.median(counts)} skills; {over} ({100 * over // len(counts)}%) loaded more than 3"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
