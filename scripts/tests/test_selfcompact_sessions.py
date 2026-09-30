#!/usr/bin/env python3
"""scripts/selfcompact-sessions: one row per session file holding records it wrote, each record
counted once. Fixtures are session JSONL files shaped as omp writes them: `{type: custom,
customType, data, id, timestamp}`, where data is the extension's v1 record without its type, as
omp/extensions/selfcompact.ts builds it. Ids are invented; timestamps are relative to now.
"""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "selfcompact-sessions"
NOW = time.time()
USAGE = {"input": 300, "output": 40, "cacheRead": 44_000, "cacheWrite": 0}


def stamp(hours_ago: float = 1.0) -> str:
    """An omp entry timestamp (JavaScript toISOString) this many hours before now."""
    t = datetime.datetime.fromtimestamp(NOW - hours_ago * 3600, datetime.timezone.utc)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def probe(verdict: str, *, capped: bool = True) -> dict:
    """A probe whose side turn replied with the rubric's four lines; uncapped when the model rejected the cap and it ran again."""
    answers = {"C1": "Y", "C2": "Y", "C3": "Y", "N1": "N"} if verdict == "compress" else {"C1": "N", "C2": "N", "C3": "N", "N1": "N"}
    return {"customType": "selfcompact-probe", "data": {"v": 1, "turn": 3, "request": 4, "tokens": 61_000, "verdict": verdict,
            "answers": answers, "probeMs": 900, "capped": capped, "stopReason": "stop", "usage": USAGE, "cost": 0.02}}


def unparsed_probe() -> dict:
    """A reply cut at the probe's token cap, so not the four lines: a continue with a reason and no answers."""
    return {"customType": "selfcompact-probe", "data": {"v": 1, "turn": 4, "request": 8, "tokens": 64_000, "verdict": "continue",
            "reason": "expected four lines, got 2", "probeMs": 1_100, "capped": True, "stopReason": "length", "usage": USAGE, "cost": 0.02}}


def failed_probe(error: str = "503 upstream unavailable") -> dict:
    """The common failure: the side turn threw, so there is no reply, no usage, no stopReason and no cost. The error
    is the thrown message, which can be empty."""
    return {"customType": "selfcompact-probe", "data": {"v": 1, "turn": 5, "request": 12, "tokens": 66_000, "verdict": "continue",
            "error": error, "probeMs": 400, "capped": False}}


def fire(ok: bool) -> dict:
    data = {"tokensBefore": 61_000, "firstKeptEntryId": "e1", "summary": "s"} if ok else {"error": "Compaction cancelled"}
    return {"customType": "selfcompact-fire", "data": {"v": 1, "turn": 3, "ok": ok, **data}}


def handler_error() -> dict:
    return {"customType": "selfcompact-error", "data": {"v": 1, "request": 5, "error": "context handler failed"}}


def session_file(directory: Path, sid: str, entries: list, *, name_stamp: str = "2026-09-30T05-00-00-000Z",
                 header_stamp: str | None = None, name: str | None = None, tail: str = "") -> Path:
    """A session file as omp writes it: the title slot, then the session header, whose timestamp is the file's
    creation (30 days ago unless given), then entries; each gets an id and a timestamp unless it carries its own."""
    directory.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "title", "v": 1, "title": "t"}, {"type": "session", "id": sid, "timestamp": header_stamp or stamp(24 * 30)}]
    lines += [{"type": "custom", "id": f"{sid[-4:]}{i:04x}", "timestamp": stamp(), **e} for i, e in enumerate(entries)]
    path = directory / (name or f"{name_stamp}_{sid}.jsonl")
    path.write_text("".join(json.dumps(line) + "\n" for line in lines) + tail)
    return path


class Sessions(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sessions"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_script(self, *args: str, root: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT), *args, *(["--sessions-root", str(self.root)] if root else [])],
                              env={"PATH": "/usr/bin:/bin", "HOME": self.tmp.name}, capture_output=True, text=True, timeout=60)

    def test_each_probe_is_one_outcome_and_every_failure_has_its_own_column(self) -> None:
        prose = {"type": "message", "message": {"role": "user", "content": "selfcompact-probe in prose, not an entry"}}
        path = session_file(self.root / "-src-x", "01a0ffff-0000-7000-8000-000000000003",
                            [probe("compress"), fire(ok=True), probe("continue"), unparsed_probe(), probe("continue", capped=False),
                             probe("compress"), fire(ok=True), probe("compress"), fire(ok=False), failed_probe(), failed_probe(error=""),
                             handler_error()],
                            # a live session's last line can be partial
                            tail=json.dumps(prose) + "\n" + '{"type": "custom", "customType": "selfcompact-fire", "da')
        session_file(self.root / "-src-y", "01a0ffff-0000-7000-8000-000000000004", [])
        r = self.run_script("--days", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        # eight probes, one outcome each; each compress fired, and one fire failed; share_length is one of the five capped replies
        self.assertEqual(r.stdout.splitlines(), [f"{path.relative_to(self.root)}  probes=8 compress=3 continue=2 unparsed=1 failed_probes=2 "
                                                 "fires=3 failed_fires=1 handler_errors=1 share_length=0.20"])
        # a record of a shape this reader does not know stops it, naming the file, rather than counting it as zeros
        v2 = session_file(self.root / "-src-z", "01a0ffff-0000-7000-8000-000000000008",
                          [{"customType": "selfcompact-fire", "data": {"v": 2, "turn": 3, "outcome": "compacted"}}])
        r = self.run_script("--days", "1")
        self.assertEqual((r.returncode, r.stdout), (2, ""), r.stderr)
        self.assertIn(str(v2), r.stderr)
        self.assertIn("version 2", r.stderr)

    def test_a_record_omp_copied_into_a_fork_branch_or_tan_clone_counts_once(self) -> None:
        # /fork, /branch, --fork and /tan copy a session's entries, same id and timestamp, into a new file under a new
        # session header; /tan writes <parent>/Tan-<id>.jsonl and runs as a subagent, so it never records anything of
        # its own (selfcompact.ts:182) -- its clone holds copies only; /fork copies the parent's artifact directory with it
        written = [{"id": "a1b2c3d4", "timestamp": stamp(4), **probe("compress")}, {"id": "a1b2c3d5", "timestamp": stamp(4), **fire(ok=True)}]
        parent = session_file(self.root / "-src-x", "01a0ffff-0000-7000-8000-000000000006", written, header_stamp=stamp(5))
        tan = session_file(parent.with_suffix(""), "01a0ffff-0000-7000-8000-00000000000a", written,
                           header_stamp=stamp(3), name="Tan-123456789.jsonl")
        fork = session_file(self.root / "-src-x", "01a0ffff-0000-7000-8000-000000000007",
                            # a copy older than the window (30h ago, outside --days 1) is not counted as left out
                            written + [{"timestamp": stamp(1), **failed_probe()}, {"timestamp": stamp(30), **probe("continue")}],
                            name_stamp="2026-09-30T06-00-00-000Z", header_stamp=stamp(2))
        shutil.copytree(parent.with_suffix(""), fork.with_suffix(""))
        own = {
            parent: "probes=1 compress=1 continue=0 unparsed=0 failed_probes=0 fires=1 failed_fires=0 handler_errors=0 share_length=0.00",
            fork: "probes=1 compress=0 continue=0 unparsed=0 failed_probes=1 fires=0 failed_fires=0 handler_errors=0 share_length=-",
        }
        r = self.run_script("--days", "1")
        self.assertEqual(r.stdout.splitlines(), [f"{f.relative_to(self.root)}  {row}" for f, row in own.items()])
        # tan (copies only) and fork's own byte copy of tan print no row, but their copied records are not silently dropped
        self.assertIn("left out 6 record(s) copied between session files (3 files)", r.stderr)
        # /delete removes the parent with its artifact directory: the fork's copies of it still count nothing
        parent.unlink()
        shutil.rmtree(parent.with_suffix(""))
        r = self.run_script("--days", "1")
        self.assertEqual(r.stdout.splitlines(), [f"{fork.relative_to(self.root)}  {own[fork]}"])
        self.assertIn("left out 4 record(s) copied between session files (2 files)", r.stderr)

    def test_a_main_session_copied_by_a_harness_counts_once(self) -> None:
        # a harness (Legion's acceptance suite copies this way) duplicates a main session's transcript, header and
        # all, into another session's own local/ scratch. The header rule cannot see it -- both the header and the
        # entries keep their original relative age -- so the key dedupe is the only thing that stops it counting twice
        held = [{"id": "h1", "timestamp": stamp(4), **probe("compress")}, {"id": "h2", "timestamp": stamp(4), **fire(ok=True)}]
        source = session_file(self.root / "-src-x", "01a0ffff-0000-7000-8000-0000000000f0", held, header_stamp=stamp(5))
        copy = session_file(self.root / "-src-y" / "01a0ffff-0000-7000-8000-0000000000f1" / "local" / "accept-1" / "transcripts",
                            "01a0ffff-0000-7000-8000-0000000000f0", held, header_stamp=stamp(5), name=source.name)
        r = self.run_script("--days", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(),
                         [f"{source.relative_to(self.root)}  probes=1 compress=1 continue=0 unparsed=0 failed_probes=0 fires=1 "
                          "failed_fires=0 handler_errors=0 share_length=0.00"])
        self.assertIn("left out 2 record(s) copied between session files (1 file)", r.stderr)
        self.assertTrue(copy.is_file())  # the copy itself is untouched; only its records are not recounted

    def test_the_window_counts_each_entry_by_its_own_timestamp(self) -> None:
        # a session resumed today holds its whole history, and only the window's entries count
        session_file(self.root / "-src-x", "01a0ffff-0000-7000-8000-000000000005",
                     [{"timestamp": stamp(72), **probe("continue")}, {"timestamp": stamp(1), **failed_probe()}])
        r = self.run_script("--days", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("  probes=1 compress=0 continue=0 unparsed=0 failed_probes=1 ", r.stdout)
        self.assertEqual(r.stderr, "")  # the old out-of-window entry is not a dedupe drop; nothing was left out
        self.assertIn("  probes=2 compress=0 continue=1 unparsed=0 failed_probes=1 ", self.run_script("--days", "4").stdout)
        r = self.run_script("--days", "0.02")  # about half an hour, which ends before the newest entry
        self.assertEqual(r.stdout.strip(), f"no selfcompact records of their own timestamped in the last 0.02 day(s) under {self.root}")

    def test_a_line_that_does_not_parse_is_reported_unless_it_is_the_last_one_being_written(self) -> None:
        # lines: title, header, the probe, then a complete line that is not JSON; only an unterminated last line is quiet
        path = session_file(self.root / "-src-x", "01a0ffff-0000-7000-8000-00000000000d", [probe("continue")],
                            tail='{"type": "custom", "customType": "selfcompact-probe", "da\n')
        r = self.run_script("--days", "1")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"{path.relative_to(self.root)}  probes=1 compress=0 continue=1 unparsed=0 failed_probes=0 "
                                                 "fires=0 failed_fires=0 handler_errors=0 share_length=0.00"])
        self.assertIn(f"{path.relative_to(self.root)}:4", r.stderr)

    def test_by_default_it_reads_every_place_omp_keeps_sessions(self) -> None:
        home = Path(self.tmp.name) / ".omp"
        main = session_file(home / "agent/sessions/-src-x", "01a0ffff-0000-7000-8000-000000000009", [probe("continue")])
        profile = session_file(home / "profiles/work/agent/sessions/-src-x", "01a0ffff-0000-7000-8000-00000000000b", [failed_probe()])
        # a --session transcript lives wherever it was pointed, under any name, and omp registers it with a marker
        elsewhere = session_file(Path(self.tmp.name) / "anywhere", "01a0ffff-0000-7000-8000-00000000000c", [fire(ok=False)],
                                 name="named-by-hand")
        registry = home / "agent/custom-session-files"
        registry.mkdir(parents=True)
        (registry / "0001").write_text(f"{elsewhere}\n")
        (registry / "0002").write_text(f"{Path(self.tmp.name) / 'gone.jsonl'}\n")  # its transcript was deleted
        link = home / "profiles-link"
        link.symlink_to(home / "profiles")
        (registry / "0003").write_text(f"{link / 'work' / 'agent' / 'sessions' / '-src-x' / profile.name}\n")
        # a cross-profile --session: registered under the default agent dir (its own profile's root is outside the
        # default's own sessions root, session-paths.ts:283) through a symlinked spelling, and also found by
        # walking that profile's root under its real path; the same file reached by two strings is read once, silently
        r = self.run_script("--days", "1", root=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        aliased = link / "work" / "agent" / "sessions" / "-src-x" / profile.name  # "-link" sorts before "/work", so this name wins
        self.assertEqual(r.stdout.splitlines(), [
            f"{main.relative_to(home)}  probes=1 compress=0 continue=1 unparsed=0 failed_probes=0 fires=0 failed_fires=0 "
            "handler_errors=0 share_length=0.00",
            f"{aliased.relative_to(home)}  probes=1 compress=0 continue=0 unparsed=0 failed_probes=1 fires=0 failed_fires=0 "
            "handler_errors=0 share_length=-",
            f"{elsewhere}  probes=0 compress=0 continue=0 unparsed=0 failed_probes=0 fires=1 failed_fires=1 handler_errors=0 share_length=-"])
        self.assertEqual(r.stderr, "")

    def test_a_selfcompact_record_with_no_preceding_session_header_stops_the_reader(self) -> None:
        # agent scratch files under local/ have no session header; if one ever names a selfcompact
        # customType past its first two lines, the reader stops rather than guessing whether it is a copy
        stray = self.root / "-src-x" / "local" / "scratch.jsonl"
        stray.parent.mkdir(parents=True)
        lines = [{"type": "title", "v": 1, "title": "t"}, {"type": "message", "role": "user", "content": "hi"},
                 {"type": "custom", "id": "z1", "timestamp": stamp(), **probe("continue")}]
        stray.write_text("".join(json.dumps(line) + "\n" for line in lines))
        r = self.run_script("--days", "1")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertEqual(r.stdout, "")
        self.assertIn(f"{stray}: selfcompact entries, but no session header", r.stderr)

    def test_a_late_deadline_reply_does_not_count_toward_share_length(self) -> None:
        # the 20 s probe deadline can pass after a reply arrives; it is capped and cut at the token cap,
        # but it is a failed probe (it has an error), not a reply to the rubric -- share_length excludes
        # it, matching the legend's "of the capped replies that arrived in time"
        late = {"customType": "selfcompact-probe", "data": {"v": 1, "turn": 6, "request": 13, "tokens": 67_000,
                "verdict": "continue", "error": "probe deadline", "probeMs": 20_000, "capped": True,
                "stopReason": "length", "usage": USAGE, "cost": 0.02}}
        session_file(self.root / "-src-x", "01a0ffff-0000-7000-8000-00000000000e", [probe("continue"), late])
        r = self.run_script("--days", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(" failed_probes=1 fires=0 failed_fires=0 handler_errors=0 share_length=0.00", r.stdout)

    def test_a_transcript_gone_when_read_is_skipped_not_a_crash(self) -> None:
        # a registered transcript can vanish between listing and reading (most are test runs that live seconds);
        # a dangling link under the root is listed by the walk and gone when read, with no race to arrange
        gone = self.root / "-src-a" / "2026-09-30T04-00-00-000Z_gone.jsonl"
        gone.parent.mkdir(parents=True)
        gone.symlink_to(self.root / "deleted.jsonl")
        kept = session_file(self.root / "-src-b", "01a0ffff-0000-7000-8000-0000000000f2", [probe("continue")])
        r = self.run_script("--days", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"{kept.relative_to(self.root)}  probes=1 compress=0 continue=1 unparsed=0 "
                                                 "failed_probes=0 fires=0 failed_fires=0 handler_errors=0 share_length=0.00"])


if __name__ == "__main__":
    unittest.main()
