"""chief-dashboard check SCOREBOARD.json    what the page will show for a scoreboard, and its format errors
chief-dashboard serve --socket PATH      serve the page
chief-dashboard snapshot OUT.json        read every source once and write the snapshot (for debugging)
chief-dashboard dispatch-check (--project KEY | --all-streams) [--issue [PROJECT:]KEY ...] [--audit KEY:slug ...]
                                        a project's Dispatch work that needs its lead or Sami, and a re-drive of
                                        every row that waits on us: an executor, a message and a time to
                                        acknowledge by (dispatch_check.py, redrive.py; --redrive-dry-run first)
chief-dashboard lead-times (--project KEY | --all-streams) [--days 7]
                                        how long a project's issues and pull requests took, to set dates from (lead_times.py)
chief-dashboard sami-messages [--since ISO8601] > FILE.md
                                        Sami's messages to the chief's session, verbatim (sami_messages.py)

Without uv: python3 -m chief_dashboard <command> from ~/.dotfiles/chief-dashboard."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(prog="chief-dashboard")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check a scoreboard.json before you upload it")
    c.add_argument("file", type=Path)
    s = sub.add_parser("serve")
    s.add_argument("--socket", type=Path, required=True)
    d = sub.add_parser("snapshot")
    d.add_argument("out", type=Path)
    sub.add_parser("dispatch-check", add_help=False, help="Dispatch work that needs a lead: run with -h for options")
    sub.add_parser("lead-times", add_help=False, help="measured issue and PR times to set dates from: -h for options")
    sub.add_parser("sami-messages", add_help=False, help="his messages to a session, verbatim: -h for options")
    args, rest = p.parse_known_args()
    if args.cmd == "dispatch-check":
        from .dispatch_check import main as dispatch_check

        sys.exit(dispatch_check(rest))
    if args.cmd == "lead-times":
        from .lead_times import main as lead_times

        sys.exit(lead_times(rest))
    if args.cmd == "sami-messages":
        from .sami_messages import main as sami_messages

        sys.exit(sami_messages(rest))
    if rest:
        p.error(f"unrecognized arguments: {' '.join(rest)}")
    if args.cmd == "check":
        from .check import main as check

        sys.exit(check(str(args.file)))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.cmd == "serve":
        from .server import serve

        serve(args.socket)
    else:
        from .collect import VersionCache, collect

        args.out.write_text(json.dumps(collect(VersionCache()), indent=1))


if __name__ == "__main__":
    main()
