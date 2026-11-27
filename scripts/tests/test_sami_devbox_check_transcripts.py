#!/usr/bin/env python3
"""devbox/sami-devbox-check's tq_own_sessions_check: does the store hold this machine's own sessions?

The line FAILs while the transcript store is behind this machine's session files. The store held no
session file of sami-devbox newer than 2026-11-12 for a fortnight while the check reported the
store's staleness as a note (dispatch://INFRA-5287). Whether the store holds a session is
judged by the ids of the TRANSCRIPT_PROBES newest top-level session files under
~/.omp/agent/sessions that started more than TRANSCRIPT_LAG_S ago. A read that finds no such
file, and a tq that fails, FAIL as well, because each of them compared nothing.

Technique, as test_sami_devbox_check_logins.py: the real script sourced without its dispatch and
the function run alone, with HOME a temporary directory. The function prepends ~/.local/bin to
PATH, so the stub tq is put there. The stub answers the id query with the ids of a "store" file
that the query names, so a query that names no probe is answered with nothing.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "devbox" / "sami-devbox-check"
LAG = timedelta(seconds=30000)

# The store: one id per line in $STORE. The id query is answered with each of those ids that the
# query text names, under the header TSVWithNames writes. Any other query is the newest-start
# query. $TQ_FAIL makes every call fail as tq does when the store cannot be reached.
TQ = r"""#!/bin/bash
[[ "$1" == sql ]] || exit 2
echo "tq: warning: last load from machcsl-gcp-1 14 days ago" >&2
if [[ -n "${TQ_FAIL:-}" ]]; then echo "tq: the store did not answer" >&2; exit 1; fi
printf '%s\n' "$2" >>"$STUB_DIR/queries"
if [[ "$2" == *"splitByChar('@', session_id)"* ]]; then
    echo id
    while read -r id; do [[ -n "$id" && "$2" == *"'$id'"* ]] && echo "$id"; done <"$STORE"
else
    echo 'max(started_at)'
    echo '2026-11-12 17:34:25.525'
fi
"""


def session_name(start: datetime, n: int) -> str:
    return f"{start.strftime('%Y-%m-%dT%H-%M-%S')}-000Z_01a1{n:04x}-0000-7000-8000-{n:012x}.jsonl"


def session_id(name: str) -> str:
    return name.split("_", 1)[1].removesuffix(".jsonl")


class OwnSessionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.sessions = self.home / ".omp" / "agent" / "sessions"
        self.sessions.mkdir(parents=True)
        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "tq").write_text(TQ)
        (bin_dir / "tq").chmod(0o755)
        self.store = self.home / "store"
        self.store.write_text("")
        self.env = dict(os.environ, HOME=str(self.home), STORE=str(self.store), STUB_DIR=str(self.home))
        self.now = datetime.now(UTC)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def add(self, age: timedelta, n: int, cwd: str = "-src") -> str:
        """A top-level session file that started `age` ago, as omp names one."""
        name = session_name(self.now - age, n)
        (self.sessions / cwd).mkdir(exist_ok=True)
        (self.sessions / cwd / name).write_text('{"type":"session"}\n')
        return name

    def hold(self, *names: str) -> None:
        self.store.write_text("".join(session_id(n) + "\n" for n in names))

    def run_line(self, **env: str) -> tuple[list[str], int]:
        body = f"source <(sed '/^case \"${{1:-}}\" in/,$d' {SCRIPT}); tq_own_sessions_check; echo \"fails=$fails\""
        out = subprocess.run(["bash", "-c", body], env={**self.env, **env}, cwd=self.home,
                             capture_output=True, text=True, timeout=60)
        lines = [line for line in out.stdout.splitlines() if "this machine's sessions" in line]
        fails = int(out.stdout.rsplit("fails=", 1)[1].strip())
        return lines, fails

    def test_a_store_that_holds_every_probe_passes(self) -> None:
        names = [self.add(LAG + timedelta(hours=h), h, cwd=f"-dir{h % 3}") for h in range(1, 6)]
        self.hold(*names)
        lines, fails = self.run_line()
        self.assertEqual(fails, 0, lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("ok "), lines)
        self.assertIn("holds each of the 5 newest", lines[0])
        self.assertIn(max(names), lines[0])  # the newest probe: the latest start

    def test_a_store_behind_this_machine_fails_and_names_what_it_lacks(self) -> None:
        old = self.add(LAG + timedelta(days=15), 1)
        newer = self.add(LAG + timedelta(hours=2), 2)
        newest = self.add(LAG + timedelta(hours=1), 3)
        self.hold(old)
        lines, fails = self.run_line()
        self.assertEqual(fails, 1, lines)
        self.assertTrue(lines[0].startswith("FAIL "), lines)
        self.assertIn("lacks 2 of the 3 newest", lines[0])
        self.assertIn(f"newest it lacks: {newest}", lines[0])
        self.assertNotIn(newer, lines[0])
        self.assertIn("started at 2026-11-12 17:34:25.525", lines[0])

    def test_a_session_younger_than_the_bar_is_not_asked_for(self) -> None:
        held = self.add(LAG + timedelta(hours=1), 1)
        self.add(LAG - timedelta(minutes=30), 2)  # not in the store yet, and need not be
        self.hold(held)
        lines, fails = self.run_line()
        self.assertEqual(fails, 0, lines)
        self.assertIn(held, lines[0])

    def test_only_the_newest_probes_are_asked_for(self) -> None:
        names = [self.add(LAG + timedelta(minutes=10 * i), i) for i in range(1, 61)]
        self.hold(*names[:50])  # the 50 youngest starts
        lines, fails = self.run_line()
        self.assertEqual(fails, 0, lines)
        self.assertIn("holds each of the 50 newest", lines[0])
        query = (self.home / "queries").read_text()
        for name in names[50:]:
            self.assertNotIn(session_id(name), query)

    def test_subagent_files_and_other_names_are_not_probes(self) -> None:
        held = self.add(LAG + timedelta(hours=3), 1)
        parent = self.sessions / "-src" / held.removesuffix(".jsonl")
        parent.mkdir()
        (parent / session_name(self.now - LAG - timedelta(hours=1), 2)).write_text("{}\n")  # depth 3: a subagent
        (self.sessions / "-src" / "notes.jsonl").write_text("{}\n")
        (self.sessions / "-src" / "2026-11-20T16-55-50-382Z_not-an-id.jsonl").write_text("{}\n")
        self.hold(held)
        lines, fails = self.run_line()
        self.assertEqual(fails, 0, lines)
        self.assertIn("holds each of the 1 newest", lines[0])

    def test_finding_no_session_file_fails_rather_than_passes(self) -> None:
        self.add(LAG - timedelta(minutes=5), 1)
        lines, fails = self.run_line()
        self.assertEqual(fails, 1, lines)
        self.assertIn("found no session file under", lines[0])
        self.assertFalse((self.home / "queries").exists(), "the store was asked about nothing")

    def test_a_tq_that_fails_fails_the_line(self) -> None:
        self.hold(self.add(LAG + timedelta(hours=1), 1))
        lines, fails = self.run_line(TQ_FAIL="1")
        self.assertEqual(fails, 1, lines)
        self.assertIn("tq sql exited 1; stderr: tq: the store did not answer", lines[0])


if __name__ == "__main__":
    unittest.main()
