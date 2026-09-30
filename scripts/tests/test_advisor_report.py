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
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "advisor-report"
FIXTURES = Path(__file__).parent / "fixtures" / "advisor-report"
WATCH_SESSION = "2026-09-20T10-00-00-000Z_01a0f000-0000-7000-8000-00000000f001"
ASK_ID = "0192f0e1-aaaa-4bbb-8ccc-0123456789ab"

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


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


_entry_seq = [0]


def gate_entry(outcome="verdict", decision="allow", latency_ms=1000, advisor="AskGate", at=None, session="s1"):
    _entry_seq[0] += 1
    return {
        "type": "custom",
        "customType": "advisor-gate",
        "id": f"g{_entry_seq[0]:05d}",
        "parentId": None,
        "timestamp": iso(at or now_utc()),
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
        "_session": session,
    }


def write_jsonl(path: Path, entries) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps({k: v for k, v in entry.items() if k != "_session"}) + "\n")


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
        header = {"type": "session", "version": 3, "id": sid, "timestamp": iso(started), "cwd": "/home/user/gate"}
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
        build_stats_db(stats, user_messages=[(str(root_file), "p0001", at, 1, 1, 0, 0, 0)])
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

    def test_out_of_charter_share_over_admitted_notes(self):
        self.assertAlmostEqual(self.metrics["watch"]["out_of_charter_pct"], 2 / 3)

    def test_held_latency_is_card_minus_advise_call(self):
        self.assertAlmostEqual(self.metrics["watch"]["held_pct"], 1 / 3)
        self.assertEqual(self.metrics["watch"]["held_latency_ms"], 470_000)

    def test_moot_when_a_later_call_carries_the_notes_ask_id_before_the_card(self):
        self.assertEqual(self.metrics["watch"]["moot_pct"], 1.0)

    def test_skips_counted_from_primary_tool_results(self):
        self.assertEqual(self.metrics["skips"], 1)

    def test_unparsable_lines_are_counted_not_fatal(self):
        self.assertEqual(self.metrics["files"]["bad_lines"], 1)

    def test_corrections_per_100_primary_turns(self):
        self.assertEqual(self.metrics["corrections"]["per_100_turns"], 10.0)


class OutOfCharterTest(unittest.TestCase):
    def call(self, update):
        return {"id": "n", "note": "x", "severity": None, "at": now_utc(), "update": update, "ack": "delivered"}

    def test_note_whose_update_has_no_scoped_call_is_out_of_charter(self):
        calls = [self.call("### Session update\n→ read(src/a.ts) ⇒ ok")]
        self.assertEqual(ar.out_of_charter_pct(calls, ar.DEFAULT_SCOPE_REGEX), 1.0)

    def test_note_whose_update_writes_dispatch_ask_is_in_charter(self):
        calls = [self.call("### Session update\n→ write(xd://dispatch_ask) ⇒ ok · 1 line")]
        self.assertEqual(ar.out_of_charter_pct(calls, ar.DEFAULT_SCOPE_REGEX), 0.0)


class ScopedCallsTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()

    def tearDown(self):
        self.home.cleanup()

    def assistant(self, call_id, name, arguments, minute, day=datetime(2026, 9, 20, 12, tzinfo=timezone.utc)):
        return {
            "type": "message", "id": f"m{call_id}", "timestamp": iso(day + timedelta(minutes=minute)),
            "message": {"role": "assistant", "content": [
                {"type": "toolCall", "id": call_id, "name": name, "arguments": arguments}]},
        }

    def metrics(self, stats):
        proc = self.home.run("metrics", "--since", "2026-09-20T00:00:00Z", "--until", "2026-09-21T00:00:00Z",
                             "--stats-db", str(stats), "--no-sync", "--json", check_exit=0)
        return json.loads(proc.stdout)

    def test_dispatch_issue_counts_only_with_spec_and_calls_split_by_file_depth(self):
        day = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
        root = self.home.session("01a0f000-0000-7000-8000-00000000f002", [
            self.assistant("t1", "write", {"path": "xd://dispatch_issue", "content": json.dumps({"title": "x"})}, 1),
            self.assistant("t2", "write", {"path": "xd://dispatch_issue", "content": json.dumps({"title": "x", "spec": "s"})}, 2),
            self.assistant("t3", "write", {"path": "xd://dispatch_search", "content": "{}"}, 3),
        ], started=day)
        sub = root.with_suffix("") / "Worker.jsonl"
        write_jsonl(sub, [self.assistant("t4", "dispatch_comment", {"issue": "EX-1", "body": "b"}, 4)])
        stats = self.home.root / "stats.db"
        build_stats_db(stats)
        scoped = self.metrics(stats)["scoped_calls"]
        self.assertEqual((scoped.get("root"), scoped.get("subagent")), (1, 1))
        self.assertEqual(scoped["per_day"].get("root"), 1.0)

    def test_corrections_recorded_before_the_session_moved_directory_still_join(self):
        """An agent box relaunch moves the session to a new project dir; stats.db keeps the earlier rows under the old path."""
        day = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
        comment = {"path": "xd://dispatch_comment", "content": json.dumps({"issue": "EX-1", "body": "b"})}
        root = self.home.session("01a0f000-0000-7000-8000-00000000f005", [
            self.assistant("t5", "write", comment, 1), self.assistant("t6", "write", comment, 2),
        ], project="-boxes-agentbox-new-example", started=day)
        old_root = self.home.sessions / "-boxes-agentbox-old-example" / root.name
        stats = self.home.root / "stats.db"
        at = int(day.timestamp() * 1000)
        build_stats_db(stats, user_messages=[(str(old_root), "u1", at, 1, 1, 0, 0, 0)], files=[old_root, root])
        self.assertEqual(self.metrics(stats)["corrections"]["sum"], 2)

    def test_gate_cost_per_day_prints_beside_the_watch_advisors(self):
        day = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
        spent = []
        for cost in (0.5, 1.5):
            entry = gate_entry(at=day, session="s")
            entry["data"]["usage"] = {"input": 1, "output": 1, "cacheRead": 0, "cacheWrite": 0, "cost": cost}
            spent.append(entry)
        root = self.home.session("01a0f000-0000-7000-8000-00000000f006", spent, started=day)
        transcripts = root.with_suffix("")
        at, outside = int(day.timestamp() * 1000), int((day - timedelta(days=3)).timestamp() * 1000)
        stats = self.home.root / "stats.db"
        build_stats_db(stats, messages=[
            (str(transcripts / "__advisor.askgate.jsonl"), "advisor", at, 3.0),
            (str(transcripts / "__advisor.askgate.jsonl"), "advisor", at, 4.0),
            (str(transcripts / "__advisor.askgate.jsonl"), "advisor", outside, 100.0),
            (str(transcripts / "__advisor.forkgate.jsonl"), "advisor", at, 50.0),
            (str(root), "main", at, 9.0),
        ])
        m = self.metrics(stats)
        self.assertAlmostEqual(m["gate"]["cost_usd_per_day"], 2.0)
        self.assertAlmostEqual(m["watch"]["cost_usd_per_day"], 7.0)


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
        first["data"].update(usage={"input": 1000, "output": 50, "cacheRead": 400, "cacheWrite": 900, "cost": 0.25},
                             argsDigest="{}", promptBytes=9000)
        second["data"].update(usage={"input": 2000, "output": 70, "cacheRead": 0, "cacheWrite": 0, "cost": 0.5})
        metrics = ar.gate_metrics([first, second, third])
        self.assertEqual(metrics["usage"], {"input": 3000, "output": 120, "cacheRead": 400, "cacheWrite": 900, "cost": 0.75})
        self.assertEqual(metrics["matched"], 3)


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


class ReadoutTest(unittest.TestCase):
    """`readout --check gate` against a launched gate, labels ingested through the real sample packet."""

    def setUp(self):
        self.home = Home()
        self.now = now_utc()
        self.launch(days=5)

    def launch(self, days: float):
        self.launched = self.now - timedelta(days=days)
        self.home.launch(gate={"launched": iso(self.launched), "timeout_ms": 90_000})

    def tearDown(self):
        self.home.cleanup()

    def gate_session(self, entries):
        return self.home.session("01a0f000-0000-7000-8000-00000000f003", entries)

    def healthy_gate(self, extra=()):
        """30 revises, 8 allows, 2 timeouts in the last hours: ungated_share 0.05."""
        at = [self.now - timedelta(minutes=5 + i) for i in range(40)]
        entries = [gate_entry(decision="revise", at=at[i], latency_ms=4000) for i in range(30)]
        entries += [gate_entry(at=at[30 + i], latency_ms=3000) for i in range(8)]
        entries += [gate_entry(outcome="timeout", at=at[38 + i], latency_ms=90_000) for i in range(2)]
        self.gate_session([*extra, *entries])

    def label_revises(self, correct: int, other: int, harmful: int = 0, unlabelled: int = 0):
        packet = self.home.root / "packet.jsonl"
        self.home.run("sample", "--since", iso(self.launched), "--until", iso(self.now), "--n", "100",
                      "--out", str(packet), check_exit=0)
        rows = [json.loads(line) for line in packet.read_text(encoding="utf-8").splitlines()][1:]
        revises = [row["id"] for row in rows if row.get("decision") == "revise"]
        self.assertEqual(len(revises), correct + harmful + other + unlabelled)
        labels = self.home.root / "labels.jsonl"
        with labels.open("w", encoding="utf-8") as fh:
            for i, row_id in enumerate(revises[:correct + harmful + other]):
                label = "acted-correct" if i < correct else "acted-harmful" if i < correct + harmful else "ignored-agent-right"
                fh.write(json.dumps({"id": row_id, "label": label, "labeler": "oracle-test", "at": iso(self.now)}) + "\n")
        self.home.run("ingest-labels", str(labels), check_exit=0)

    def test_fewer_than_30_labels_is_incomplete(self):
        self.healthy_gate()
        proc = self.home.run("readout", "--check", "gate", check_exit=3)
        self.assertIn("labels < 30", proc.stdout)
        self.assertFalse(self.home.overlay.exists())

    def test_precision_on_30_labels_with_low_ungated_share_is_no_finding(self):
        self.healthy_gate()
        self.label_revises(correct=18, other=12)
        proc = self.home.run("readout", "--check", "gate", check_exit=0)
        self.assertIn("precision 0.60", proc.stdout)
        self.assertFalse(self.home.overlay.exists())

    def kill_fixture(self):
        at = [self.now - timedelta(minutes=10 + i) for i in range(20)]
        self.gate_session([gate_entry(at=at[i]) for i in range(15)]
                          + [gate_entry(outcome="timeout", at=at[15 + i], latency_ms=90_000) for i in range(5)])

    def test_day_three_ungated_share_over_20_percent_applies_the_kill(self):
        self.kill_fixture()
        self.home.run("readout", "--check", "gate", check_exit=1)
        self.assert_killed()

    def test_day_three_verdicts_at_85_s_against_a_90_s_timeout_apply_the_kill(self):
        """A verdict slower than the timeout is recorded as a timeout, so the stall shows as p95 near it."""
        def verdicts_at(latency_ms):
            self.gate_session([gate_entry(at=self.now - timedelta(minutes=10 + i), latency_ms=latency_ms) for i in range(20)])

        verdicts_at(80_000)
        self.home.run("readout", "--check", "gate", check_exit=3)
        self.assertFalse(self.home.overlay.exists())
        verdicts_at(85_000)
        out = self.home.run("readout", "--check", "gate", check_exit=1).stdout
        self.assertIn("p95 latency 85000 ms", out)
        self.assert_killed()

    def test_day_three_latency_kill_waits_for_20_verdicts_in_the_last_24_h(self):
        """One slow verdict among a handful is the whole p95 (nearest rank); it must not end the trial."""
        latencies = [85_000, 3000, 3000, 3000, 3000]
        self.gate_session([gate_entry(at=self.now - timedelta(minutes=10 + i), latency_ms=ms) for i, ms in enumerate(latencies)])
        out = self.home.run("readout", "--check", "gate", check_exit=3).stdout
        self.assertIn("p95 85000 ms", out)
        self.assertFalse(self.home.overlay.exists())

    def killed_after_a_day_three_burst(self):
        """230 good calls, a day-3 hour with 8 timeouts in 25 calls, then 250 calls the kill switch passed."""
        day3 = self.launched + timedelta(days=3)
        entries = [gate_entry(decision="revise" if i < 30 else "allow", at=self.launched + timedelta(minutes=5 + 18 * i),
                              latency_ms=4000) for i in range(230)]
        entries += [gate_entry(outcome="timeout" if i < 8 else "verdict", at=day3 + timedelta(minutes=2 * i),
                               latency_ms=90_000 if i < 8 else 4000) for i in range(25)]
        entries += [gate_entry(outcome="killed", at=day3 + timedelta(hours=2 + i), latency_ms=0) for i in range(250)]
        self.gate_session(entries)

    def test_after_a_kill_a_later_readout_computes_no_go(self):
        self.launch(days=15)
        self.killed_after_a_day_three_burst()
        self.label_revises(correct=18, other=12)
        self.home.overlay.write_text("advisor:\n  disableRoster:\n  - askgate\n", encoding="utf-8")
        before = self.home.overlay.read_bytes()
        proc = self.home.run("readout", "--check", "gate", check_exit=1)
        self.assertIn("precision 0.60", proc.stdout)
        self.assertIn("no GO or EXTEND computed", proc.stdout)
        self.assertNotIn("GO:", proc.stdout)
        self.assertEqual(self.home.overlay.read_bytes(), before)
        # The S9 drill: the kill switch passes two calls, then the owner removes the member and the gate runs on.
        self.home.run("overlay", "remove", "advisor.disableRoster", "askgate", check_exit=0)
        shutil.rmtree(self.home.sessions)
        self.healthy_gate(extra=self.s9_drill())
        self.label_revises(correct=18, other=12)
        proc = self.home.run("readout", "--check", "gate", check_exit=0)
        self.assertIn("(2 killed)", proc.stdout)
        self.assertIn("gate: GO: make OMP_ASKGATE=block the shim default", proc.stdout)
        self.assertEqual(self.home.overlay_doc(), {"advisor": {"disableRoster": []}})

    def s9_drill(self):
        return [gate_entry(outcome="killed", at=self.launched + timedelta(hours=6, minutes=i), latency_ms=0) for i in range(2)]

    def test_an_s9_drill_on_day_half_is_incomplete_not_killed(self):
        self.launch(days=0.5)
        self.healthy_gate(extra=self.s9_drill())
        proc = self.home.run("readout", "--check", "gate", check_exit=3)
        self.assertIn("(2 killed)", proc.stdout)
        self.assertIn("gate: incomplete: labels < 30", proc.stdout)
        self.assertFalse(self.home.overlay.exists())

    def test_an_overlay_holding_askgate_computes_no_go(self):
        self.launch(days=15)
        self.healthy_gate()
        self.label_revises(correct=18, other=12)
        self.home.overlay.write_text("advisor:\n  disableRoster:\n  - askgate\n", encoding="utf-8")
        proc = self.home.run("readout", "--check", "gate", check_exit=1)
        self.assertIn("no GO or EXTEND computed", proc.stdout)
        self.assertNotIn("GO:", proc.stdout)

    def readout_on_day(self, day: float, correct: int, other: int, harmful: int = 0, unlabelled: int = 0,
                       exit_code: int = 0) -> str:
        """The healthy gate (30 revises, ungated_share 0.05, p95 4 s, no skips) read out `day` days after launch."""
        self.launch(days=day)
        self.healthy_gate()
        self.label_revises(correct=correct, other=other, harmful=harmful, unlabelled=unlabelled)
        return self.home.run("readout", "--check", "gate", check_exit=exit_code).stdout

    def assert_killed(self):
        self.assertEqual(self.home.overlay_doc(), {"advisor": {"disableRoster": ["askgate"]}})

    def test_day_14_precision_0_6_is_go(self):
        out = self.readout_on_day(15, correct=18, other=12)
        self.assertIn("gate: GO: make OMP_ASKGATE=block the shim default", out)
        self.assertFalse(self.home.overlay.exists())

    def test_day_14_precision_0_4_extends(self):
        out = self.readout_on_day(15, correct=12, other=18)
        self.assertIn("gate: EXTEND: one more week", out)
        self.assertFalse(self.home.overlay.exists())

    def test_day_14_precision_0_2_kills(self):
        out = self.readout_on_day(15, correct=6, other=24, exit_code=1)
        self.assertIn("precision 0.20 < 0.3", out)
        self.assert_killed()

    def test_day_14_harm_0_13_kills(self):
        out = self.readout_on_day(15, correct=18, harmful=4, other=8, exit_code=1)
        self.assertIn("harm 0.13 > 0.1", out)
        self.assert_killed()

    def test_day_14_harm_over_0_10_kills_at_20_labels(self):
        out = self.readout_on_day(15, correct=10, harmful=5, other=5, unlabelled=10, exit_code=1)
        self.assertIn("n=20", out)
        self.assertIn("harm 0.25 > 0.1", out)
        self.assert_killed()

    def test_day_22_not_go_kills(self):
        out = self.readout_on_day(22, correct=12, other=18, exit_code=1)
        self.assertIn("day 21: not GO after the one-week extension", out)
        self.assert_killed()

    def test_day_14_ungated_share_0_125_kills_without_labels(self):
        self.launch(days=15)
        at = [self.now - timedelta(minutes=5 + i) for i in range(40)]
        entries = [gate_entry(decision="revise", at=at[i], latency_ms=4000) for i in range(30)]
        entries += [gate_entry(at=at[30 + i], latency_ms=3000) for i in range(5)]
        entries += [gate_entry(outcome="timeout", at=at[35 + i], latency_ms=90_000) for i in range(5)]
        self.gate_session(entries)
        out = self.home.run("readout", "--check", "gate", check_exit=1).stdout
        self.assertIn("ungated_share 0.125 >= 0.1", out)
        self.assert_killed()

    def test_gate_and_trial_checks_combine_their_exits(self):
        self.healthy_gate()
        proc = self.home.run("readout", "--check", "gate", "--check", "trial", check_exit=3)
        self.assertIn("trial: not armed", proc.stdout)

    def test_a_gate_kill_wins_over_an_unarmed_trial(self):
        self.kill_fixture()
        self.home.run("readout", "--check", "gate", "--check", "trial", check_exit=1)

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

    def test_an_extension_link_outside_dotfiles_is_incomplete_and_writes_nothing(self):
        self.healthy_gate()
        self.label_revises(correct=18, other=12)
        stray = self.home.root / "elsewhere" / "askgate.ts"
        stray.parent.mkdir()
        stray.write_text("export default function askgate() {}\n", encoding="utf-8")
        self.home.extension_link.unlink()
        self.home.extension_link.symlink_to(stray)
        proc = self.home.run("readout", "--check", "gate", check_exit=3)
        self.assertIn(str(self.home.extension_link), proc.stdout)
        self.assertFalse(self.home.overlay.exists())
        self.home.extension_link.unlink()
        self.home.run("readout", "--check", "gate", check_exit=3)


class SampleTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()

    def tearDown(self):
        self.home.cleanup()

    def test_revises_fill_at_most_half_the_packet_and_the_header_records_the_strata(self):
        now = now_utc()
        context = [
            {"type": "message", "id": f"u{i}", "timestamp": iso(now - timedelta(hours=3, minutes=i)),
             "message": {"role": "assistant", "content": [{"type": "text", "text": f"step {i}"}]}}
            for i in range(20, 0, -1)
        ]
        gates = [gate_entry(decision="revise", at=now - timedelta(minutes=100 - i)) for i in range(40)]
        gates += [gate_entry(at=now - timedelta(minutes=50 - i)) for i in range(20)]
        self.home.session("01a0f000-0000-7000-8000-00000000f004", context + gates)
        packet = self.home.root / "packet.jsonl"
        self.home.run("sample", "--since", iso(now - timedelta(days=1)), "--until", iso(now), "--n", "20",
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
