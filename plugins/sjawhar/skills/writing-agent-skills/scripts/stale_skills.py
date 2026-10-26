#!/usr/bin/env python3
"""List skills that no session has loaded recently (writing-agent-skills, section 5).

Usage: stale_skills.py [--days N] [skills dir]
A skill counts as loaded when a session ran a successful `read` of `skill://<name>`, as recorded
in the transcript store (`tq sql`, skill://using-transcript-store). Blind spots: hosts the store
does not ingest, Claude Code's Skill tool, and skill text pasted into a brief. A skill with
`disable-model-invocation: true` is marked `manual`, never STALE, because no agent loads it on
its own. Prints one TSV row per skill, the stale ones first. Standard library only.
"""

import argparse
import datetime
import subprocess
import sys
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[2]  # plugins/sjawhar/skills
LOADS = (
    "select extract(command, 'skill://([A-Za-z0-9_-]+)') as skill, count() as loads, "
    "max(started_at) as last from calls where tool = 'read' "
    "and match(command, 'skill://[A-Za-z0-9_-]+') and not ifNull(is_error, false) group by skill"
)
COVERAGE = "select toDate(min(started_at)) from calls"


def tq(query: str) -> list[list[str]]:
    out = subprocess.run(["tq", "sql", "--format", "TSVWithNames", query], stdout=subprocess.PIPE, text=True, check=True)
    return [line.split("\t") for line in out.stdout.splitlines()[1:]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("skills_dir", nargs="?", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--days", type=int, default=28, help="stale when not loaded for this many days (default 28)")
    args = parser.parse_args()
    cutoff = (datetime.date.today() - datetime.timedelta(days=args.days)).isoformat()
    first = tq(COVERAGE)[0][0]
    if first > cutoff:
        print(f"# warning: the store starts on {first}, after the cutoff {cutoff}; STALE means not loaded since {first}",
              file=sys.stderr)
    loads = {skill: (int(count), last[:10]) for skill, count, last in tq(LOADS)}
    rows = []
    for skill_md in sorted(args.skills_dir.expanduser().glob("*/SKILL.md")):
        name = skill_md.parent.name
        count, last = loads.get(name, (0, "never"))
        if "disable-model-invocation: true" in skill_md.read_text().split("\n---", 1)[0]:
            status = "manual"
        else:
            status = "STALE" if last == "never" or last < cutoff else "ok"
        rows.append((status != "STALE", status, last, name, count))
    print("status\tskill\tloads\tlast_loaded")
    for _, status, last, name, count in sorted(rows):
        print(f"{status}\t{name}\t{count}\t{last}")
    print(f"# {sum(r[1] == 'STALE' for r in rows)} of {len(rows)} STALE (cutoff {cutoff})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
