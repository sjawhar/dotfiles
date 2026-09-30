#!/usr/bin/env python3
"""scripts/advisor-report: the advisor metrics, the sample packet, the readout and the overlay.

The watch-mode contracts run against the synthetic session pair under
fixtures/advisor-report; the gate, readout and overlay contracts build their
sessions, stats.db and roster in a temporary HOME, so nothing reads the real ~/.omp.
"""
from __future__ import annotations

import fcntl
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "advisor-report"
FIXTURES = Path(__file__).parent / "fixtures" / "advisor-report"
WATCH_SESSION = "2026-09-20T10-00-00-000Z_01a0f000-0000-7000-8000-00000000f001"

ROSTER_OK = """advisors:
  - name: ForkGate
  - name: General
"""

# scripts/envoy as it is: `send --source` alone is refused naming the flag; everything else is logged.
ENVOY_STUB = """#!/bin/sh
if [ "$*" = "send --source" ]; then echo "--source accepts only envoy" >&2; exit 2; fi
printf '%s\\n' "$*" >> "$STUB_LOG"
"""

# scripts/envoy before `--source` existed: `send --source envoy <topic> <msg>` publishes to the
# topic "--source", and `send --source` alone is a usage error.
ENVOY_WITHOUT_SOURCE = """#!/bin/sh
if [ $# -lt 3 ]; then echo "Usage: envoy send <target> <message>" >&2; exit 1; fi
printf '%s\\n' "$*" >> "$STUB_LOG"
"""


def load_module():
    loader = importlib.machinery.SourceFileLoader("advisor_report", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


ar = None


def setUpModule():
    global ar
    ar = load_module()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


_entry_seq = [0]


def gate_line(outcome="verdict", decision="allow", latency_ms=1000, advisor="AskGate", at=None):
    """An `advisor-gate` entry as the AskGate extension appends it to a root session file."""
    _entry_seq[0] += 1
    return {
        "type": "custom",
        "customType": "advisor-gate",
        "id": f"g{_entry_seq[0]:05d}",
        "parentId": None,
        "timestamp": ar.iso(at or now_utc()),
        "data": {
            "advisor": advisor,
            "tool": "write",
            "path": "xd://example_send",
            "toolCallId": f"call{_entry_seq[0]}",
            "decision": decision,
            "outcome": outcome,
            "verdictMode": "warn",
            "latencyMs": latency_ms,
            "revisesForKey": 0,
            **({"reason": "failure 7: the text points at a message the reader cannot see"} if decision == "revise" else {}),
        },
    }


def gate_entry(session="s1", **fields):
    """The same entry as the script reads it."""
    line = gate_line(**fields)
    return ar.GateEntry(session_id=session, id=line["id"], at=ar.parse_iso(line["timestamp"]), data=line["data"])


def write_jsonl(path: Path, entries) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def build_stats_db(path: Path, user_messages=(), files=(), messages=(), covers_until_ms=None) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        create table user_messages (session_file text, entry_id text, timestamp integer, negation integer, blame integer,
                                    anguish integer, yelling integer, profanity integer);
        create table file_offsets (session_file text, last_modified real);
        create table messages (session_file text, agent_type text, timestamp integer, cost_total real);
        """
    )
    db.executemany("insert into user_messages values (?, ?, ?, ?, ?, ?, ?, ?)", user_messages)
    db.executemany("insert into messages values (?, ?, ?, ?)", messages)
    hwm = covers_until_ms if covers_until_ms is not None else time.time() * 1000
    db.executemany("insert into file_offsets values (?, ?)", [(str(f), hwm) for f in files] or [("x", hwm)])
    db.commit()
    db.close()


class Home:
    """A temporary HOME with ~/.omp, a dotfiles dir holding the roster, the AskGate extension link and an envoy stub."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.agent_dir = self.root / ".omp" / "agent"
        self.sessions = self.agent_dir / "sessions"
        self.sessions.mkdir(parents=True)
        self.report_dir = self.root / ".omp" / "advisor-report"
        self.overlay = self.agent_dir / "local-overrides.yml"
        self.dotfiles = self.root / "dotfiles"
        (self.dotfiles / "omp" / "extensions").mkdir(parents=True)
        (self.dotfiles / "scripts").mkdir()
        self.roster = self.dotfiles / "omp" / "WATCHDOG.yml"
        self.roster.write_text(ROSTER_OK, encoding="utf-8")
        extension = self.dotfiles / "omp" / "extensions" / "askgate.ts"
        extension.write_text("export default function askgate() {}\n", encoding="utf-8")
        self.extension_link = self.agent_dir / "extensions" / "askgate.ts"
        self.extension_link.parent.mkdir()
        self.extension_link.symlink_to(extension)
        envoy = self.dotfiles / "scripts" / "envoy"
        envoy.write_text(ENVOY_STUB, encoding="utf-8")
        envoy.chmod(0o755)
        self.envoy_log = self.root / "envoy.log"
        self.env = {
            "HOME": str(self.root),
            "PATH": os.environ["PATH"],
            "DOTFILES_DIR": str(self.dotfiles),
            "STUB_LOG": str(self.envoy_log),
        }

    def cleanup(self):
        self.tmp.cleanup()

    def run(self, *args, check_exit=None):
        proc = subprocess.run([str(SCRIPT), *args], env=self.env, capture_output=True, text=True, check=False)
        if check_exit is not None and proc.returncode != check_exit:
            raise AssertionError(f"exit {proc.returncode} != {check_exit}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        return proc

    def session(self, sid: str, entries, project="-home-user-gate", started=None) -> Path:
        started = started or now_utc() - timedelta(days=6)
        stem = started.strftime("%Y-%m-%dT%H-%M-%S-000Z") + f"_{sid}"
        path = self.sessions / project / f"{stem}.jsonl"
        header = {"type": "session", "version": 3, "id": sid, "timestamp": ar.iso(started), "cwd": "/home/user/gate"}
        write_jsonl(path, [header, *entries])
        return path

    def launch(self, **records):
        self.report_dir.mkdir(parents=True, exist_ok=True)
        (self.report_dir / "launch.json").write_text(json.dumps(records), encoding="utf-8")

    def overlay_doc(self):
        import yaml

        return yaml.safe_load(self.overlay.read_text(encoding="utf-8"))


class AckTest(unittest.TestCase):
    def test_six_exact_ack_strings(self):
        self.assertEqual(ar.classify_ack("Delivered."), "delivered")
        self.assertEqual(ar.classify_ack("Queued for the end of the turn. Do not re-raise."), "queued")
        self.assertEqual(ar.classify_ack("Dropped: empty note."), "dropped-empty")
        self.assertEqual(ar.classify_ack("Dropped: nothing actionable."), "dropped-noise")
        self.assertEqual(ar.classify_ack("Dropped: already raised."), "dropped-duplicate")
        self.assertEqual(ar.classify_ack("Dropped: this update's advice budget is spent."), "dropped-budget")
        self.assertEqual(ar.classify_ack("Delivered. Extra"), "unknown")


class WatchFixtureTest(unittest.TestCase):
    """`metrics --json` over the synthetic watch-mode session pair."""

    @classmethod
    def setUpClass(cls):
        cls.home = Home()
        shutil.copytree(FIXTURES / "sessions", cls.home.sessions, dirs_exist_ok=True)
        root_file = cls.home.sessions / "-home-user-example" / f"{WATCH_SESSION}.jsonl"
        stats = cls.home.root / "stats.db"
        at = int(datetime(2026, 9, 20, 10, 0, 5, tzinfo=timezone.utc).timestamp() * 1000)
        transcript = root_file.with_suffix("") / "__advisor.askgate.jsonl"
        build_stats_db(stats, user_messages=[(str(root_file), "p0001", at, 1, 1, 0, 0, 0)],
                       messages=[(str(transcript), "advisor", at, 3.0), (str(transcript), "advisor", at, 4.0)])
        proc = cls.home.run(
            "metrics", "--since", "2026-09-20T00:00:00Z", "--until", "2026-09-21T00:00:00Z", "--advisor", "askgate",
            "--stats-db", str(stats), "--no-sync", "--json", check_exit=0,
        )
        cls.metrics = json.loads(proc.stdout)

    @classmethod
    def tearDownClass(cls):
        cls.home.cleanup()

    def test_notes_per_100_turns(self):
        self.assertEqual(self.metrics["primary_turns"], 20)
        self.assertEqual(self.metrics["watch"]["admitted"], 3)
        self.assertEqual(self.metrics["watch"]["notes_per_100_turns"], 15.0)

    def test_advise_calls_by_ack(self):
        self.assertEqual(self.metrics["watch"]["advise_calls"], 4)
        self.assertEqual(self.metrics["watch"]["by_ack"], {"delivered": 2, "queued": 1, "dropped-duplicate": 1})
        self.assertEqual(self.metrics["watch"]["advisor_turns"], 4)

    def test_out_of_charter_share_over_admitted_notes(self):
        self.assertAlmostEqual(self.metrics["watch"]["out_of_charter_pct"], 2 / 3)

    def test_held_latency_is_card_minus_advise_call(self):
        self.assertAlmostEqual(self.metrics["watch"]["held_pct"], 1 / 3)
        self.assertEqual(self.metrics["watch"]["held_latency_ms"], 470_000)

    def test_moot_when_a_later_call_carries_the_notes_ask_id_before_the_card(self):
        self.assertEqual(self.metrics["watch"]["moot_pct"], 1.0)

    def test_a_skip_counts_for_an_advisor_only_when_its_own_card_caused_it(self):
        # The fixture's one skip follows the AskGate card; the card came first, so it did not cause the skip.
        self.assertEqual((self.metrics["skips"], self.metrics["skips_any_steer"]), (0, 1))

    def test_unparsable_lines_are_counted_not_fatal(self):
        self.assertEqual(self.metrics["files"]["bad_lines"], 1)

    def test_corrections_per_100_primary_turns(self):
        self.assertEqual(self.metrics["corrections"]["per_100_turns"], 10.0)

    def test_watch_advisor_cost_per_day(self):
        self.assertAlmostEqual(self.metrics["watch"]["cost_usd_per_day"], 7.0)


T0 = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
ASK = "0192f0e1-aaaa-4bbb-8ccc-0123456789ab"


def minutes(n: float | None) -> datetime | None:
    return None if n is None else T0 + timedelta(minutes=n)


def hour() -> "ar.Window":
    """The window every inline metric test reads: T0 to T0 + 60 minutes."""
    return ar.Window(minutes(0), minutes(60))


def advise(note="note", at=0, ack="queued", severity=None, update=""):
    return ar.AdviseCall(id=f"call-{note}", note=note, severity=severity, at=minutes(at), update=update, ack=ack)


def card(at, *notes, position=0):
    return ar.Card(position=position, at=minutes(at), notes=frozenset(notes))


def tool(at, arguments, name="write"):
    return ar.ToolCall(at=minutes(at), id=f"t{at}", name=name, arguments=arguments)


def record(**fields):
    return ar.SessionRecord(path=Path("s.jsonl"), session_id="s", **fields)


def assistant_call(call_id, name, arguments, at):
    return {"type": "message", "id": f"m{call_id}", "timestamp": ar.iso(at),
            "message": {"role": "assistant", "content": [{"type": "toolCall", "id": call_id, "name": name, "arguments": arguments}]}}


def skipped_result(call_id, at):
    """A tool result the fork skipped because a steer was pending."""
    return {"type": "message", "id": f"r{call_id}", "timestamp": ar.iso(at),
            "message": {"role": "toolResult", "toolCallId": call_id, "toolName": "wait",
                        "content": [{"type": "text", "text": "Skipped due to pending system advisory: a steer arrived."}]}}


def envoy_steer(at):
    return {"type": "custom_message", "customType": "envoy-message", "content": "envoy:\n  from: a peer", "display": True,
            "timestamp": ar.iso(at)}


def card_steer(at, *advisors):
    """An advisor card with one note from each advisor; None is the unnamed legacy advisor, which names none."""
    notes = [{"note": f"note {i}", "severity": "concern", **({} if name is None else {"advisor": name})} for i, name in enumerate(advisors)]
    return {"type": "custom_message", "customType": "advisor", "content": "<advisory>…</advisory>", "display": True,
            "details": {"notes": notes}, "timestamp": ar.iso(at)}


def skipped_by(steer, at, call_id):
    """The agent calls a tool, the fork skips it for a pending steer, and the steer follows the skipped result."""
    return [assistant_call(call_id, "wait", {}, at), skipped_result(call_id, at + timedelta(seconds=1)), steer]


class SkipAttributionTest(unittest.TestCase):
    """The fork writes the same skip text for every non-user steer; the steer that caused a skip is the first custom
    message after the skipped result, before the agent's next assistant message."""

    def skips(self, entries):
        return ar.parse_primary(record(), entries, "askgate").skips

    def test_each_skip_takes_the_steer_that_follows_it(self):
        skips = self.skips([
            *skipped_by(envoy_steer(minutes(1)), minutes(1), "w1"),
            *skipped_by(card_steer(minutes(2), "AskGate"), minutes(2), "w2"),
            *skipped_by(card_steer(minutes(3), None), minutes(3), "w3"),
        ])
        self.assertEqual([(skip.steer, skip.advisors) for skip in skips],
                         [("envoy-message", frozenset()), ("advisor", frozenset({"askgate"})), ("advisor", frozenset({"default"}))])

    def test_a_card_before_the_skip_or_after_the_next_turn_did_not_cause_it(self):
        skips = self.skips([
            card_steer(minutes(1), "AskGate"), assistant_call("w1", "wait", {}, minutes(1)), skipped_result("w1", minutes(1)),
            assistant_call("a2", "read", {}, minutes(2)), card_steer(minutes(2), "AskGate"),
        ])
        self.assertEqual([skip.steer for skip in skips], [None])

    def test_parallel_skips_share_the_one_steer(self):
        skips = self.skips([assistant_call("w1", "wait", {}, minutes(1)), skipped_result("w1", minutes(1)),
                            skipped_result("w2", minutes(1)), card_steer(minutes(1), "Memory", "AskGate")])
        self.assertEqual([skip.advisors for skip in skips], [frozenset({"memory", "askgate"})] * 2)

    def test_only_the_advisors_own_cards_count(self):
        skips = [ar.Skip(minutes(5), "envoy-message"), ar.Skip(minutes(6), "advisor", frozenset({"askgate"})),
                 ar.Skip(minutes(7), "advisor", frozenset({"memory"})), ar.Skip(minutes(61), "advisor", frozenset({"askgate"})),
                 ar.Skip(minutes(8), None)]
        self.assertEqual(ar.own_skips(record(skips=skips), hour(), "askgate"), 1)
        self.assertEqual(ar.skips_in(record(skips=skips), hour()), 4)


class WindowedCountsTest(unittest.TestCase):
    """Each count takes what falls inside [since, until]: a second before `since` or after `until` is out."""

    EDGES = (-1 / 60, 0, 30, 60, 60 + 1 / 60)

    def test_primary_turns(self):
        self.assertEqual(ar.primary_turns(record(turn_times=[minutes(m) for m in self.EDGES] + [None]), hour()), 3)

    def test_advisor_turns(self):
        self.assertEqual(ar.advisor_turns(record(advisor_turn_times=[minutes(m) for m in self.EDGES]), hour()), 3)

    def test_advise_calls(self):
        calls = [advise(str(m), m) for m in self.EDGES] + [advise("untimed", None)]
        self.assertEqual([call.note for call in ar.window_calls(record(advise_calls=calls), hour())], ["0", "30", "60"])

    def test_root_scoped_calls_count_dispatch_issue_only_with_spec(self):
        spec = {"path": "xd://dispatch_issue", "content": json.dumps({"title": "x", "spec": "s"})}
        calls = [
            tool(-1 / 60, spec), tool(1, spec),
            tool(2, {"path": "xd://dispatch_issue", "content": json.dumps({"title": "x"})}),
            tool(3, {"path": "xd://dispatch_search", "content": "{}"}),
            tool(4, {"issue": "EX-1", "body": "b"}, name="dispatch_comment"),
            tool(60 + 1 / 60, spec),
        ]
        self.assertEqual(ar.root_scoped_calls(record(tool_calls=calls), hour(), re.compile(ar.DEFAULT_SCOPE_REGEX)), 2)


class HeldNotesTest(unittest.TestCase):
    def test_latency_runs_to_the_first_later_card_carrying_the_exact_note(self):
        note = advise(f"Ask {ASK} names the retired flag", at=0)
        cards = [card(-1, note.note), card(2, "another note"), card(5, note.note, position=7), card(9, note.note)]
        held = ar.held_notes([note], cards)
        self.assertEqual((held.latencies_ms, held.unrouted), ([300_000], 0))
        self.assertEqual(held.routed, [(note, cards[2])])

    def test_a_queued_note_no_card_carries_is_unrouted(self):
        held = ar.held_notes([advise("carried"), advise("never carried")], [card(1, "carried")])
        self.assertEqual((len(held.latencies_ms), held.unrouted), (1, 1))

    def test_only_queued_notes_are_held_and_an_untimed_card_routes_nothing(self):
        held = ar.held_notes([advise("delivered", ack="delivered"), advise("untimed")], [card(1, "delivered"), card(None, "untimed")])
        self.assertEqual((held.latencies_ms, held.unrouted, held.routed), ([], 1, []))


class MootTest(unittest.TestCase):
    """A routed note is moot when a primary call between the note and its card carries one of the note's ids."""

    def moot(self, text, *calls):
        note = advise(text, at=0)
        return ar.moot_count([(note, card(10, text))], list(calls))

    def test_a_call_before_the_card_carrying_the_notes_id_makes_it_moot(self):
        self.assertEqual(self.moot(f"Edit ask {ASK}", tool(5, {"path": "xd://dispatch_edit_ask", "content": json.dumps({"ask": ASK})})), 1)

    def test_a_call_without_the_id_does_not(self):
        self.assertEqual(self.moot(f"Edit ask {ASK}", tool(5, {"command": "ls"}, name="bash")), 0)

    def test_a_call_after_the_card_does_not(self):
        self.assertEqual(self.moot(f"Edit ask {ASK}", tool(11, {"content": json.dumps({"ask": ASK})})), 0)

    def test_a_note_without_ids_is_never_moot(self):
        self.assertEqual(self.moot("Run the tests first", tool(5, {"command": "run the tests first"}, name="bash")), 0)


class ChainsTest(unittest.TestCase):
    def test_notes_at_jaccard_one_half_are_a_pair_and_below_it_are_not(self):
        self.assertEqual(ar.chain_pairs([advise("retire old flag"), advise("retire old switch")]), 1)
        self.assertEqual(ar.chain_pairs([advise("retire old flag"), advise("retire new switch")]), 0)

    def test_blockers(self):
        self.assertEqual(ar.blockers([advise(severity="blocker"), advise(severity="blocker"), advise(severity="concern")]), 2)


class OutOfCharterTest(unittest.TestCase):
    def test_note_whose_update_has_no_scoped_call_is_out_of_charter(self):
        calls = [advise(update="### Session update\n→ read(src/a.ts) ⇒ ok", ack="delivered")]
        self.assertEqual(ar.out_of_charter(calls, re.compile(ar.DEFAULT_SCOPE_REGEX)), 1)

    def test_note_whose_update_writes_dispatch_ask_is_in_charter(self):
        calls = [advise(update="### Session update\n→ write(xd://dispatch_ask) ⇒ ok · 1 line", ack="delivered")]
        self.assertEqual(ar.out_of_charter(calls, re.compile(ar.DEFAULT_SCOPE_REGEX)), 0)


class SessionFilesTest(unittest.TestCase):
    """Root sessions are sessions/<project>/<stem>.jsonl; every other file below <stem>/ but an advisor transcript is a subagent."""

    def test_files_split_by_where_they_sit(self):
        home = Home()
        self.addCleanup(home.cleanup)
        comment = {"path": "xd://dispatch_comment", "content": json.dumps({"issue": "EX-1", "body": "b"})}
        sid = "01a0f000-0000-7000-8000-00000000f002"
        root = home.session(sid, [assistant_call("t1", "write", comment, T0)], started=T0)
        write_jsonl(root.with_suffix("") / "Worker.jsonl", [
            assistant_call("t2", "dispatch_comment", {"issue": "EX-1", "body": "b"}, T0),
            assistant_call("t3", "dispatch_comment", {"issue": "EX-1", "body": "b"}, T0 + timedelta(days=2)),
        ])
        write_jsonl(root.with_suffix("") / "__advisor.askgate.jsonl", [assistant_call("t4", "write", comment, T0)])
        window = ar.Window(T0 - timedelta(days=1), T0 + timedelta(days=1))
        self.assertEqual(list(ar.root_session_files(home.sessions, window, "askgate")), [(root, sid)])
        subagents = ar.subagent_scoped_calls(home.sessions, window, re.compile(ar.DEFAULT_SCOPE_REGEX))
        self.assertEqual((subagents.files, subagents.calls), (1, 1))


class StatsDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "stats.db"

    def ms(self, at: datetime) -> int:
        return int(at.timestamp() * 1000)

    def test_corrections_sum_the_windows_rows_of_root_sessions_through_a_move(self):
        stem = f"2026-09-20T11-00-00-000Z_{ASK}.jsonl"
        root = Path("/s/-boxes-agentbox-new-example") / stem
        build_stats_db(self.path, user_messages=[
            (f"/s/-boxes-agentbox-old-example/{stem}", "u1", self.ms(minutes(1)), 1, 1, 0, 0, 0),
            (str(root), "u2", self.ms(minutes(2)), 0, 0, 1, 0, 0),
            ("/s/-other/2026-09-20T11-00-00-000Z_other.jsonl", "u3", self.ms(minutes(3)), 5, 0, 0, 0, 0),
            (str(root), "u4", self.ms(minutes(61)), 7, 0, 0, 0, 0),
        ])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(ar.corrections(db, [root], hour()), 3)

    def test_watcher_cost_sums_the_advisors_transcript_rows_in_the_window(self):
        transcripts = Path("/s/-p/2026-09-20T11-00-00-000Z_x")
        build_stats_db(self.path, messages=[
            (str(transcripts / "__advisor.askgate.jsonl"), "advisor", self.ms(minutes(1)), 3.0),
            (str(transcripts / "__advisor.askgate.jsonl"), "advisor", self.ms(minutes(2)), 4.0),
            (str(transcripts / "__advisor.askgate.jsonl"), "advisor", self.ms(minutes(-60)), 100.0),
            (str(transcripts / "__advisor.forkgate.jsonl"), "advisor", self.ms(minutes(1)), 50.0),
            (f"{transcripts}.jsonl", "main", self.ms(minutes(1)), 9.0),
        ])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(ar.watcher_cost(db, hour(), "askgate"), 7.0)

    def test_watch_baseline_is_the_watch_askgates_spend_per_day_over_the_14_days_before_launch(self):
        transcript = "/s/-p/2026-09-01T00-00-00-000Z_x/__advisor.askgate.jsonl"
        build_stats_db(self.path, messages=[
            (transcript, "advisor", self.ms(T0 - timedelta(days=3)), 14.0),
            (transcript, "advisor", self.ms(T0 - timedelta(days=20)), 1000.0),
            (transcript, "advisor", self.ms(T0 + timedelta(hours=1)), 1000.0),
        ])
        self.assertEqual(ar.watch_baseline(self.path, T0), "watch-mode AskGate $1.00/day over the 14 days before launch")
        self.assertEqual(ar.watch_baseline(self.path.with_name("absent.db"), T0),
                         f"no stats db at {self.path.with_name('absent.db')} for the watch-mode baseline")


class SampleUnitTest(unittest.TestCase):
    def test_revise_verdicts_and_blocker_notes_are_the_priority_stratum(self):
        self.assertEqual(ar.sample_stratum(gate_entry(decision="revise")), "priority")
        self.assertEqual(ar.sample_stratum(gate_entry(decision="revise", outcome="timeout")), "other")
        self.assertEqual(ar.sample_stratum(gate_entry()), "other")
        self.assertEqual(ar.sample_stratum(advise(severity="blocker", ack="delivered")), "priority")
        self.assertEqual(ar.sample_stratum(advise(severity="concern", ack="delivered")), "other")

    def test_context_is_five_primary_messages_either_side(self):
        entries = []
        for i in range(12):
            entries.append({"type": "message", "id": f"u{i}", "timestamp": ar.iso(minutes(i)),
                            "message": {"role": "assistant", "content": [{"type": "text", "text": f"step {i}"}]}})
            entries.append({"type": "custom", "customType": "tool_execution_start", "id": f"x{i}"})
        rendered = ar.context_around(entries, entries.index(next(e for e in entries if e["id"] == "u6")))
        self.assertEqual([row["text"] for row in rendered], [f"step {i}" for i in (1, 2, 3, 4, 5, 7, 8, 9, 10, 11)])


class MissingLabelWeeksTest(unittest.TestCase):
    def test_names_each_completed_week_whose_revises_carry_no_label(self):
        fired = [("gate:s:a", T0 + timedelta(days=1)), ("gate:s:b", T0 + timedelta(days=8)),
                 ("gate:s:c", T0 + timedelta(days=14.5))]
        labels = {"gate:s:a": {"id": "gate:s:a", "label": "moot"}}
        self.assertEqual(ar.missing_label_weeks(T0, T0 + timedelta(days=15), fired, labels),
                         ["week 2 (2026-09-27..2026-10-04)"])

    def test_a_week_without_revises_is_not_named(self):
        fired = [("gate:s:a", T0 + timedelta(days=8))]
        self.assertEqual(ar.missing_label_weeks(T0, T0 + timedelta(days=22), fired, {}), ["week 2 (2026-09-27..2026-10-04)"])


class GateMetricsTest(unittest.TestCase):
    def test_latency_percentiles_over_verdicts_and_fail_open_rate(self):
        entries = [gate_entry(latency_ms=ms) for ms in (1000, 2000, 3000, 40_000)]
        entries += [gate_entry(outcome="timeout", latency_ms=90_000), gate_entry(outcome="error", latency_ms=5)]
        # a rebuttal, a breaker pass and an unavailable advisor never reach the model, so they are not in the denominator
        entries += [gate_entry(outcome="rebuttal"), gate_entry(outcome="breaker"), gate_entry(outcome="unavailable")]
        metrics = ar.gate_metrics(entries)
        self.assertAlmostEqual(metrics["fail_open_rate"], 2 / 6)
        self.assertEqual(metrics["latency_ms_p50"], 2000)
        self.assertEqual(metrics["latency_ms_p95"], 40_000)

    def test_a_halted_session_counts_every_later_call_as_ungated(self):
        entries = [gate_entry(outcome="timeout") for _ in range(3)] + [gate_entry(outcome="halted") for _ in range(50)]
        metrics = ar.gate_metrics(entries)
        self.assertEqual(metrics["ungated_share"], 1.0)
        self.assertEqual(metrics["matched"], 53)
        self.assertEqual(metrics["fail_open_rate"], 1.0)
        self.assertEqual(metrics["halted_sessions"], 1)

    def test_calls_the_kill_switch_passed_are_not_counted_as_ungated(self):
        entries = [gate_entry() for _ in range(18)] + [gate_entry(outcome="timeout") for _ in range(2)]
        entries += [gate_entry(outcome="killed") for _ in range(10)]
        metrics = ar.gate_metrics(entries)
        self.assertAlmostEqual(metrics["ungated_share"], 2 / 20)
        self.assertEqual(metrics["killed"], 10)

    def test_usage_sums_each_entrys_tokens_and_cost_and_extra_fields_are_ignored(self):
        first, second, third = gate_entry(), gate_entry(), gate_entry(outcome="timeout")
        first.data.update(usage={"input": 1000, "output": 50, "cacheRead": 400, "cacheWrite": 900, "cost": 0.25},
                          argsDigest="{}", promptBytes=9000)
        second.data.update(usage={"input": 2000, "output": 70, "cacheRead": 0, "cacheWrite": 0, "cost": 0.5})
        metrics = ar.gate_metrics([first, second, third])
        self.assertEqual(metrics["usage"], {"input": 3000, "output": 120, "cacheRead": 400, "cacheWrite": 900, "cost": 0.75})
        self.assertEqual(metrics["matched"], 3)

    def test_revise_rate_is_over_verdicts(self):
        entries = [gate_entry(decision="revise"), gate_entry(), gate_entry(outcome="timeout"), gate_entry(outcome="rebuttal")]
        self.assertEqual(ar.gate_metrics(entries)["revise_rate"], 0.5)


class OverlayTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()

    def tearDown(self):
        self.home.agent_dir.chmod(0o755)
        self.home.cleanup()

    def members(self):
        return self.home.overlay_doc()["advisor"]["disableRoster"]

    def test_a_failed_write_leaves_the_old_file_byte_identical(self):
        self.home.run("overlay", "add", "advisor.disableRoster", "askgate", check_exit=0)
        before = self.home.overlay.read_bytes()
        self.home.agent_dir.chmod(0o555)
        proc = self.home.run("overlay", "add", "advisor.disableRoster", "memory")
        self.home.agent_dir.chmod(0o755)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.home.overlay.read_bytes(), before)
        self.assertEqual(list(self.home.agent_dir.glob("local-overrides.*.tmp")), [])

    def test_adds_are_member_semantics_in_either_order_and_remove_takes_only_its_member(self):
        self.home.overlay.write_text("compaction:\n  enabled: true\n", encoding="utf-8")
        for member in ("askgate", "memory", "drift"):
            self.home.run("overlay", "add", "advisor.disableRoster", member, check_exit=0)
        self.assertEqual(self.members(), ["askgate", "memory", "drift"])
        self.assertEqual(self.home.overlay_doc()["compaction"], {"enabled": True})
        self.home.overlay.unlink()
        for member in ("memory", "drift", "askgate", "memory"):
            self.home.run("overlay", "add", "advisor.disableRoster", member, check_exit=0)
        self.assertEqual(sorted(self.members()), ["askgate", "drift", "memory"])
        self.home.run("overlay", "remove", "advisor.disableRoster", "askgate", check_exit=0)
        self.assertEqual(self.members(), ["memory", "drift"])
        self.home.run("overlay", "remove", "advisor.disableRoster", "askgate", check_exit=0)
        self.assertEqual(self.members(), ["memory", "drift"])

    def test_concurrent_adds_lose_no_update(self):
        """Each worker adds 50 members of its own, so a write lost to a race stays lost."""
        loop = 'for i in $(seq 50); do "$0" overlay add advisor.disableRoster "$1$i" || exit 1; done'
        workers = [
            subprocess.Popen(["bash", "-c", loop, str(SCRIPT), member], env=self.home.env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            for member in ("a", "b")
        ]
        for worker in workers:
            _, err = worker.communicate(timeout=120)
            self.assertEqual(worker.returncode, 0, err)
        expected = [f"{member}{i}" for member in ("a", "b") for i in range(1, 51)]
        self.assertEqual(sorted(self.members()), sorted(expected))
        self.assertEqual(list(self.home.agent_dir.glob("local-overrides.*.tmp")), [])

    def test_an_add_waits_for_the_lock(self):
        lock_path = self.home.agent_dir / "local-overrides.yml.lock"
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            proc = subprocess.Popen([str(SCRIPT), "overlay", "add", "advisor.disableRoster", "askgate"],
                                    env=self.home.env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            time.sleep(3)  # an unlocked add finishes in well under a second, even on a loaded host
            self.assertIsNone(proc.poll(), "overlay add returned while another process held the lock")
            self.assertFalse(self.home.overlay.exists())
            fcntl.flock(lock, fcntl.LOCK_UN)
        _, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(self.members(), ["askgate"])


def gm(ungated=0.05, p95=4000, matched=40, verdicts=38):
    """The gate-metrics fields the rules read."""
    return {"ungated_share": ungated, "latency_ms_p95": p95, "matched": matched, "verdicts": verdicts}


def labelled(n=30, precision=0.6, harm=0.0):
    return {"n": n, "precision": precision, "harm": harm}


UNLABELLED = labelled(n=0, precision=None, harm=None)


class GateDecisionTest(unittest.TestCase):
    """One row per pre-registered rule (docs/advisor-report.md, Readout rules). Every row changes a gate that reads GO
    on day 15: 30 labels at precision 0.60, harm 0, ungated_share 0.05, p95 4 s against a 90 s timeout, no skips."""

    ROWS = [
        # (rule, changed inputs, verdict, text a reason carries, text no reason carries)
        ("the killed overlay comes first, even over a day-3 burst", dict(killed=True, recent=gm(ungated=0.5, matched=20)),
         "killed", "", None),
        ("before day 3 no rule runs", dict(day=2.9, recent=gm(ungated=0.5, matched=20), labelled=UNLABELLED),
         "incomplete", "labels < 30", None),
        ("day 3: ungated_share above 0.20 over 20 calls in the last 24 h kills",
         dict(day=3, recent=gm(ungated=0.25, matched=20), labelled=UNLABELLED),
         "kill", "ungated_share 0.250 > 0.2 over 20 calls in the last 24 h", None),
        ("day 3: 19 calls are under the floor", dict(day=3, recent=gm(ungated=0.5, matched=19), labelled=UNLABELLED),
         "incomplete", "", None),
        ("day 3: verdict p95 above 0.9 x the timeout over 20 verdicts kills",
         dict(day=3, recent=gm(p95=85_000, verdicts=20), labelled=UNLABELLED),
         "kill", "p95 latency 85000 ms above 0.9 x the 90000 ms timeout over 20 verdicts in the last 24 h", None),
        ("day 3: verdict p95 at 0.9 x the timeout does not", dict(day=3, recent=gm(p95=81_000, verdicts=20), labelled=UNLABELLED),
         "incomplete", "", None),
        ("day 3: 19 verdicts are under the floor", dict(day=3, recent=gm(p95=85_000, verdicts=19), labelled=UNLABELLED),
         "incomplete", "", None),
        ("the day-3 rule comes before the day-14 rules", dict(recent=gm(ungated=0.25, matched=20), whole=gm(ungated=0.2)),
         "kill", "over 20 calls in the last 24 h", "day 14"),
        ("day 14: ungated_share 0.10 over the window kills", dict(whole=gm(ungated=0.10)),
         "kill", "day 14: ungated_share 0.100 >= 0.1", None),
        ("day 14: the ungated KILL is named before precision", dict(whole=gm(ungated=0.12), labelled=labelled(precision=0.2)),
         "kill", "ungated_share 0.120", "precision"),
        ("day 14: precision below 0.3 at 30 labels kills", dict(labelled=labelled(precision=0.29)),
         "kill", "day 14: precision 0.29 < 0.3", None),
        ("day 14: precision below 0.3 at 29 labels waits", dict(labelled=labelled(n=29, precision=0.2)),
         "incomplete", "labels < 30 (n=29)", None),
        ("day 14: harm above 0.10 kills", dict(labelled=labelled(harm=0.11)), "kill", "day 14: harm 0.11 > 0.1 over 30 labels", None),
        ("day 14: harm above 0.10 kills at 20 labels", dict(labelled=labelled(n=20, precision=0.5, harm=0.25)),
         "kill", "harm 0.25 > 0.1 over 20 labels", None),
        ("day 14: GO", {}, "go", "make OMP_ASKGATE=block the shim default", None),
        ("before day 14 a GO-ready gate is on track", dict(day=13.9), "on track", "", None),
        ("day 14: a skip blocks GO", dict(skips=1), "extend", "one more week, then GO or KILL", None),
        ("day 14: p95 above 0.9 x the timeout blocks GO", dict(whole=gm(p95=81_001)), "extend", "", None),
        ("day 14: harm 0.06 blocks GO", dict(labelled=labelled(harm=0.06)), "extend", "", None),
        ("day 14: ungated_share 0.099 allows GO", dict(whole=gm(ungated=0.099)), "go", "", None),
        ("day 14: precision 0.49 extends", dict(labelled=labelled(precision=0.49)), "extend", "", None),
        ("day 14: precision 0.50 is GO", dict(labelled=labelled(precision=0.5)), "go", "", None),
        ("day 21: not GO after the extension kills", dict(day=21, labelled=labelled(precision=0.4)),
         "kill", "day 21: not GO after the one-week extension", None),
        ("day 21: GO", dict(day=22), "go", "", None),
        ("day 21: fewer than 30 labels kills", dict(day=22, labelled=labelled(n=20)),
         "kill", "day 21: 20 labelled revises, fewer than 30, after the one-week extension", None),
        ("day 21: fewer than 30 labels waits for a week without labels", dict(day=22, labelled=labelled(n=20), missing_weeks=["week 3 (x)"]),
         "incomplete", "no labels for week 3 (x)", None),
        ("before day 21 fewer than 30 labels waits", dict(day=20.9, labelled=labelled(n=20)), "incomplete", "labels < 30 (n=20)", None),
        ("a completed week with revises and no labels is incomplete, whatever n", dict(day=14.1, missing_weeks=["week 2 (x)"]),
         "incomplete", "no labels for week 2 (x)", None),
        ("a week without labels holds back no KILL", dict(missing_weeks=["week 2 (x)"], labelled=labelled(harm=0.2)),
         "kill", "harm 0.20", None),
        ("a roster or link problem blocks GO", dict(problems=["roster empty"]), "incomplete", "", None),
        ("a roster or link problem does not block a KILL", dict(problems=["roster empty"], whole=gm(ungated=0.2)),
         "kill", "ungated_share", None),
    ]

    def decide(self, **changes):
        inputs = dict(day=15, timeout_ms=90_000, whole=gm(), recent=gm(), labelled=labelled(), skips=0, missing_weeks=[],
                      killed=False, problems=[])
        return ar.gate_decision(**{**inputs, **changes})

    def test_each_rule(self):
        for rule, changes, verdict, carried, absent in self.ROWS:
            with self.subTest(rule):
                decision = self.decide(**changes)
                reasons = "; ".join(decision.reasons)
                self.assertEqual(decision.verdict, verdict, reasons)
                self.assertIn(carried, reasons)
                if absent is not None:
                    self.assertNotIn(absent, reasons)


class RosterProblemsTest(unittest.TestCase):
    def test_the_extension_link_must_resolve_under_dotfiles_and_the_roster_keep_advisors(self):
        home = Home()
        self.addCleanup(home.cleanup)
        self.assertEqual(ar.roster_problems(home.dotfiles, home.agent_dir), [])
        stray = home.root / "elsewhere" / "askgate.ts"
        stray.parent.mkdir()
        stray.write_text("export default function askgate() {}\n", encoding="utf-8")
        home.extension_link.unlink()
        home.extension_link.symlink_to(stray)
        problems = ar.roster_problems(home.dotfiles, home.agent_dir)
        self.assertEqual(len(problems), 1)
        self.assertIn(str(home.extension_link), problems[0])
        home.extension_link.unlink()
        self.assertEqual(len(ar.roster_problems(home.dotfiles, home.agent_dir)), 1)
        home.roster.write_text("advisors: []\n", encoding="utf-8")
        self.assertEqual(len(ar.roster_problems(home.dotfiles, home.agent_dir)), 2)


class ReadoutTest(unittest.TestCase):
    """`readout --check gate` against a launched gate, labels ingested through the real sample packet."""

    def setUp(self):
        self.home = Home()
        self.now = now_utc()
        self.launch(days=5)

    def launch(self, days: float):
        self.launched = self.now - timedelta(days=days)
        self.home.launch(gate={"launched": ar.iso(self.launched), "timeout_ms": 90_000})

    def tearDown(self):
        self.home.cleanup()

    def gate_session(self, entries):
        return self.home.session("01a0f000-0000-7000-8000-00000000f003", entries)

    def healthy_gate(self, extra=()):
        """30 revises, 8 allows, 2 timeouts in the last hours: ungated_share 0.05."""
        at = [self.now - timedelta(minutes=5 + i) for i in range(40)]
        entries = [gate_line(decision="revise", at=at[i], latency_ms=4000) for i in range(30)]
        entries += [gate_line(at=at[30 + i], latency_ms=3000) for i in range(8)]
        entries += [gate_line(outcome="timeout", at=at[38 + i], latency_ms=90_000) for i in range(2)]
        self.gate_session([*extra, *entries])

    def label_revises(self, correct: int, other: int, harmful: int = 0, unlabelled: int = 0, until: datetime | None = None):
        packet = self.home.root / "packet.jsonl"
        self.home.run("sample", "--since", ar.iso(self.launched), "--until", ar.iso(until or self.now), "--n", "100",
                      "--out", str(packet), check_exit=0)
        rows = [json.loads(line) for line in packet.read_text(encoding="utf-8").splitlines()][1:]
        revises = [row["id"] for row in rows if row.get("decision") == "revise"]
        self.assertEqual(len(revises), correct + harmful + other + unlabelled)
        labels = self.home.root / "labels.jsonl"
        with labels.open("w", encoding="utf-8") as fh:
            for i, row_id in enumerate(revises[:correct + harmful + other]):
                label = "acted-correct" if i < correct else "acted-harmful" if i < correct + harmful else "ignored-agent-right"
                fh.write(json.dumps({"id": row_id, "label": label, "labeler": "oracle-test", "at": ar.iso(self.now)}) + "\n")
        self.home.run("ingest-labels", str(labels), check_exit=0)

    def test_a_week_with_revises_and_no_labels_is_incomplete_whatever_n(self):
        """Day 14.1: week 1's 30 revises are labelled at precision 0.60, week 2's 30 are not labelled yet."""
        self.launch(days=14.1)
        week = timedelta(days=7)
        entries = [gate_line(decision="revise", at=self.launched + timedelta(days=1, minutes=i), latency_ms=4000) for i in range(30)]
        entries += [gate_line(decision="revise", at=self.launched + week + timedelta(days=1, minutes=i), latency_ms=4000)
                    for i in range(30)]
        self.gate_session(entries + [gate_line(at=self.now - timedelta(minutes=5 + i), latency_ms=3000) for i in range(8)])
        self.label_revises(correct=18, other=12, until=self.launched + week)
        out = self.home.run("readout", "--check", "gate", check_exit=3).stdout
        self.assertIn("labelled revises n=30, precision 0.60", out)
        self.assertIn(f"gate: incomplete: no labels for week 2 ({self.launched + week:%Y-%m-%d}..", out)
        self.assertNotIn("gate: GO", out)
        self.assertFalse(self.home.overlay.exists())

    def kill_fixture(self):
        at = [self.now - timedelta(minutes=10 + i) for i in range(20)]
        self.gate_session([gate_line(at=at[i]) for i in range(15)]
                          + [gate_line(outcome="timeout", at=at[15 + i], latency_ms=90_000) for i in range(5)])

    def test_a_kill_is_written_to_the_overlay_and_exits_1(self):
        self.kill_fixture()
        out = self.home.run("readout", "--check", "gate", check_exit=1).stdout
        self.assertIn(f"gate: KILL applied (advisor.disableRoster += askgate in {self.home.overlay})", out)
        self.assert_killed()

    def s9_drill(self):
        return [gate_line(outcome="killed", at=self.launched + timedelta(hours=6, minutes=i), latency_ms=0) for i in range(2)]

    def test_an_s9_drill_on_day_half_is_incomplete_not_killed(self):
        self.launch(days=0.5)
        self.healthy_gate(extra=self.s9_drill())
        proc = self.home.run("readout", "--check", "gate", check_exit=3)
        self.assertIn("(2 killed)", proc.stdout)
        self.assertIn("gate: incomplete: labels < 30", proc.stdout)
        self.assertFalse(self.home.overlay.exists())

    def test_an_overlay_holding_askgate_computes_no_go_and_writes_nothing(self):
        self.launch(days=15)
        self.healthy_gate()
        self.label_revises(correct=18, other=12)
        self.home.overlay.write_text("advisor:\n  disableRoster:\n  - askgate\n", encoding="utf-8")
        before = self.home.overlay.read_bytes()
        proc = self.home.run("readout", "--check", "gate", check_exit=1)
        self.assertIn("no GO or EXTEND computed", proc.stdout)
        self.assertNotIn("GO:", proc.stdout)
        self.assertEqual(self.home.overlay.read_bytes(), before)

    def readout_on_day(self, day: float, correct: int, other: int, harmful: int = 0, unlabelled: int = 0,
                       exit_code: int = 0) -> str:
        """The healthy gate (30 revises, ungated_share 0.05, p95 4 s, no skips) read out `day` days after launch."""
        self.launch(days=day)
        self.healthy_gate()
        self.label_revises(correct=correct, other=other, harmful=harmful, unlabelled=unlabelled)
        return self.home.run("readout", "--check", "gate", check_exit=exit_code).stdout

    def assert_killed(self):
        self.assertEqual(self.home.overlay_doc(), {"advisor": {"disableRoster": ["askgate"]}})

    def test_day_14_precision_0_6_from_ingested_labels_is_go(self):
        out = self.readout_on_day(15, correct=18, other=12)
        self.assertIn("labelled revises n=30, precision 0.60", out)
        self.assertIn("gate: GO: make OMP_ASKGATE=block the shim default", out)
        self.assertFalse(self.home.overlay.exists())

    def test_envoy_skips_count_for_nothing_and_a_gate_card_skip_still_counts(self):
        """Four envoy messages and one AskGate card each skipped a tool result in a GO-ready gate: the card's skip
        enters harm and blocks GO, the envoy ones do neither, so no KILL."""
        self.launch(days=15)
        at = self.now - timedelta(hours=2)
        steers = [envoy_steer(at) for _ in range(4)] + [card_steer(at, "AskGate")]
        extra = [entry for i, steer in enumerate(steers) for entry in skipped_by(steer, at + timedelta(minutes=i), f"w{i}")]
        self.healthy_gate(extra=extra)
        self.label_revises(correct=18, other=12)
        out = self.home.run("readout", "--check", "gate", check_exit=0).stdout
        self.assertIn("skips 1 ", out)
        self.assertIn("harm 0.03", out)
        self.assertIn("gate: EXTEND", out)
        self.assertFalse(self.home.overlay.exists())

    def test_gate_and_trial_checks_combine_their_exits(self):
        self.healthy_gate()
        proc = self.home.run("readout", "--check", "gate", "--check", "trial", check_exit=3)
        self.assertIn("trial: not armed", proc.stdout)

    def test_an_empty_advisors_list_messages_the_role_and_writes_nothing(self):
        self.healthy_gate()
        self.label_revises(correct=18, other=12)
        self.home.roster.write_text("advisors: []\n", encoding="utf-8")
        self.home.run("readout", "--check", "gate", "--notify", "notifications.role.example", check_exit=3)
        self.assertFalse(self.home.overlay.exists())
        sent = self.home.envoy_log.read_text(encoding="utf-8")
        self.assertTrue(sent.startswith("send --source envoy notifications.role.example advisor-report (AGENTC-1323)"), sent)
        self.assertIn(str(self.home.roster), sent)

    def test_the_readout_speaks_as_envoy(self):
        self.healthy_gate()
        self.label_revises(correct=18, other=12)
        self.home.run("readout", "--check", "gate", "--notify", "notifications.role.example", check_exit=0)
        sent = self.home.envoy_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(sent), 1, sent)
        self.assertTrue(sent[0].startswith("send --source envoy notifications.role.example advisor-report (AGENTC-1323) readout: "), sent)

    def test_an_envoy_without_source_sends_nothing_and_is_incomplete(self):
        self.healthy_gate()
        self.label_revises(correct=18, other=12)
        (self.home.dotfiles / "scripts" / "envoy").write_text(ENVOY_WITHOUT_SOURCE, encoding="utf-8")
        proc = self.home.run("readout", "--check", "gate", "--notify", "notifications.role.example", check_exit=3)
        self.assertFalse(self.home.envoy_log.exists())
        self.assertIn("send --source envoy", proc.stderr)


class SampleTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()

    def tearDown(self):
        self.home.cleanup()

    def test_revises_fill_at_most_half_the_packet_and_the_header_records_the_strata(self):
        now = now_utc()
        context = [
            {"type": "message", "id": f"u{i}", "timestamp": ar.iso(now - timedelta(hours=3, minutes=i)),
             "message": {"role": "assistant", "content": [{"type": "text", "text": f"step {i}"}]}}
            for i in range(20, 0, -1)
        ]
        gates = [gate_line(decision="revise", at=now - timedelta(minutes=100 - i)) for i in range(40)]
        gates += [gate_line(at=now - timedelta(minutes=50 - i)) for i in range(20)]
        self.home.session("01a0f000-0000-7000-8000-00000000f004", context + gates)
        packet = self.home.root / "packet.jsonl"
        self.home.run("sample", "--since", ar.iso(now - timedelta(days=1)), "--until", ar.iso(now), "--n", "20",
                      "--out", str(packet), check_exit=0)
        header, *rows = [json.loads(line) for line in packet.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(header["strata"], {"priority": {"population": 40, "drawn": 10},
                                            "other": {"population": 20, "drawn": 10}})
        self.assertEqual(sum(row["decision"] == "revise" for row in rows), 10)
        self.assertEqual(len({row["id"] for row in rows}), 20)
        self.assertTrue(all(1 <= len(row["context"]) <= 10 for row in rows))


class IngestLabelsTest(unittest.TestCase):
    def test_an_unknown_label_rejects_the_whole_file(self):
        home = Home()
        self.addCleanup(home.cleanup)
        labels = home.root / "labels.jsonl"
        labels.write_text(
            json.dumps({"id": "gate:s:g1", "label": "moot", "labeler": "o", "at": "2026-09-30T00:00:00Z"}) + "\n"
            + json.dumps({"id": "gate:s:g2", "label": "great", "labeler": "o", "at": "2026-09-30T00:00:00Z"}) + "\n",
            encoding="utf-8",
        )
        proc = home.run("ingest-labels", str(labels), check_exit=2)
        self.assertIn("great", proc.stderr)
        self.assertFalse((home.report_dir / "labels.jsonl").exists())


class ArmTest(unittest.TestCase):
    def test_arm_trial_refuses_without_a_recorded_approval(self):
        home = Home()
        self.addCleanup(home.cleanup)
        home.run("arm", "trial", "--launched", "2026-09-30T00:00:00Z", check_exit=2)
        self.assertFalse((home.root / ".omp" / "advisor-trial" / "armed").exists())


if __name__ == "__main__":
    unittest.main()
