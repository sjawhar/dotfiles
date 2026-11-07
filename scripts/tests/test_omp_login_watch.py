#!/usr/bin/env python3
"""What omp-login-watch alerts on, who hears it, and what it never reads.

The watch is the one on these boxes whose alert goes to a person: when the shared credential
store has no enabled credential for a provider its sessions use, only a login fixes it
(dispatch://INFRA-387, 2026-11-01T12:35:17Z). These tests drive it against fixture credential
stores and synthetic transcripts, so they read neither the live store nor a real session.

The `data` column of auth_credentials holds the tokens. One test puts a sentinel there and
proves no query names the column and no alert carries the value.
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import signal
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

# The script under test, and the unit files beside it. Both are overridable, because
# ~/.dotfiles is production with no staging step: a change is tested from a copy outside the
# checkout (OMP_LOGIN_WATCH_SCRIPT=<copy> OMP_LOGIN_WATCH_UNITS=<dir> python3 this file) and
# saved into the checkout only once it passes. The copy needs scripts/envoy_client.py beside
# it: the script imports the shared Envoy client from its own directory, so that a copy under
# test never reaches the live checkout's.
SCRIPT = Path(os.environ.get("OMP_LOGIN_WATCH_SCRIPT") or Path(__file__).resolve().parents[1] / "omp-login-watch")
UNITS = Path(os.environ.get("OMP_LOGIN_WATCH_UNITS") or Path(__file__).resolve().parents[2] / "omp")
LOADER = importlib.machinery.SourceFileLoader("omp_login_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

# Obviously fake, and built from parts so the public leak check, which scans each added line, does not read it as a key.
SECRET = "sk-" + "ant-oat01-" + "FAKE-TEST-ONLY-NOT-A-KEY"
CAUSE = 'oauth refresh failed: OAuthError: anthropic-console token refresh failed: 400 {"error": "invalid_grant"}'
SESSIONS = ["01a10000-0000-7000-8000-00000000000%d" % n for n in range(1, 6)]
# One ask id per ask a case opens, in order, so a crossing's second ask is told from its first.
ASK_IDS = ["%s-2222-4333-8444-555555555555" % (str(n) * 8) for n in range(1, 6)]


class FakeEnvoy:
    """Records what left over Envoy; answers no live-session list, as a listener that is down."""

    instances: list = []

    def __init__(self, timeout: float = 8.0) -> None:
        self.published: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []
        FakeEnvoy.instances.append(self)

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return True

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return True

    def live_sessions(self):
        return None


def credential_store(path: Path, rows: list[tuple[str, str | None]]) -> None:
    """A store shaped like omp's, each row (provider, disabled_cause), all data = SECRET."""
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE auth_credentials ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, credential_type TEXT NOT NULL,"
        " data TEXT NOT NULL, disabled_cause TEXT DEFAULT NULL, identity_key TEXT DEFAULT NULL,"
        " created_at INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL DEFAULT 0)"
    )
    now = int(time.time())
    connection.executemany(
        "INSERT INTO auth_credentials (provider, credential_type, data, disabled_cause, identity_key,"
        " created_at, updated_at) VALUES (?, 'oauth', ?, ?, 'email:sami@theorem.dev', ?, ?)",
        [(provider, SECRET, cause, now - 7200, now - 60) for provider, cause in rows],
    )
    connection.commit()
    connection.close()


def transcript(directory: Path, session: str, errors: int, message: str, age: float = 5.0) -> Path:
    """A session transcript whose last lines hold `errors` turns omp could not send."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"2026-11-01T12-00-00-000Z_{session}.jsonl"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - age))
    lines = [json.dumps({"type": "message", "timestamp": f"{stamp}.000Z", "message": {"role": "user"}})]
    for _ in range(errors):
        lines.append(
            '{"type":"message","timestamp":"%s.000Z","message":{"role":"assistant"},'
            '"stopReason":"error","errorMessage":"%s"}' % (stamp, message)
        )
    path.write_text("\n".join(lines) + "\n")
    os.utime(path, (time.time() - age, time.time() - age))
    return path


def options(tmp: Path, db: Path, **overrides) -> argparse.Namespace:
    values = {
        "db": str(db),
        "state": str(tmp / "state.json"),
        "sessions_store": str(db),
        "issue": "INFRA-387",
        "sessions": 3,
        "window": 300.0,
        "tail_bytes": 262144,
        "repeat": 1800.0,
        "dispatch_session": "01a10000-0000-7000-8000-0000000000ff",
        "dispatch_timeout": 30.0,
        "box_health_owner": WATCH.SRE,
        "dry_run": False,
        "no_envoy": False,
        "print_alert": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class WatchCase(unittest.TestCase):
    """Each case runs the watch with Envoy, the dispatch CLI and the process list replaced."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="omp-login-watch-test-"))
        self.sessions_root = self.tmp / "sessions"
        self.sessions_root.mkdir()
        self.calls: list[list[str]] = []
        # None: every ask succeeds with the next id of ASK_IDS. A case that sets a pair forces
        # that answer on every call, which is how a refusal is driven.
        self.answer: tuple | None = None
        self.asked = 0
        self.live: dict[str, int] = {}
        self.unnamed = 0
        self.elsewhere = 0
        FakeEnvoy.instances = []
        self.restore = {
            "Envoy": WATCH.Envoy,
            "SESSION_ROOT": WATCH.SESSION_ROOT,
            "dispatch_call": WATCH.dispatch_call,
            "omp_sessions": WATCH.omp_sessions,
            "log": WATCH.log,
        }
        for name in ("journal_tail", "REPORT_FALLBACK", "process_started"):
            if hasattr(WATCH, name):
                self.restore[name] = getattr(WATCH, name)
        WATCH.Envoy = FakeEnvoy
        WATCH.SESSION_ROOT = self.sessions_root
        WATCH.dispatch_call = self.dispatch_call
        WATCH.omp_sessions = lambda store: (dict(self.live), self.unnamed, self.elsewhere)
        WATCH.log = lambda message: None
        WATCH.journal_tail = lambda unit: "(the unit's journal, not read in a test)"
        WATCH.REPORT_FALLBACK = self.tmp / "fallback-reports"
        # A pass's budget counts from its process's start, which in a test is pytest's.
        WATCH.process_started = time.time

    def tearDown(self) -> None:
        for name, value in self.restore.items():
            setattr(WATCH, name, value)

    def dispatch_call(self, arguments, options):
        self.calls.append(list(arguments))
        if self.answer is not None:
            return self.answer
        if arguments and arguments[0] == "ask":
            identifier = ASK_IDS[min(self.asked, len(ASK_IDS) - 1)]
            self.asked += 1
            return True, f"https://dispatch.example/issues/INFRA-387/asks/{identifier}"
        return True, "done"

    def asks(self) -> list[list[str]]:
        return [call for call in self.calls if call and call[0] == "ask"]

    def alerts(self) -> list[str]:
        return [message for _, message in FakeEnvoy.instances[-1].published] if FakeEnvoy.instances else []

    def every_alert(self) -> list[str]:
        """What left over Envoy across every run of the case, not just the last one."""
        return [message for instance in FakeEnvoy.instances for _, message in instance.published]

    def report_of(self, text: str) -> str:
        """The report file an Envoy message names in its `Full report: <path>` ending."""
        self.assertLessEqual(len(text), 600, text)
        self.assertIn(" Full report: ", text)
        return Path(text.rsplit(" Full report: ", 1)[1]).read_text()

    def store(self, name: str, rows) -> Path:
        path = self.tmp / name
        credential_store(path, rows)
        return path


class CrossingOnDisabledCredentials(WatchCase):
    def test_every_row_disabled_alerts_a_person(self) -> None:
        db = self.store("down.db", [("anthropic", CAUSE), ("anthropic", CAUSE), ("openai-codex", None)])
        self.live = {SESSIONS[0]: 101, SESSIONS[1]: 102}
        WATCH.run_once(options(self.tmp, db))
        self.assertEqual(len(self.asks()), 1, "one Dispatch ask opens the crossing")
        question = self.asks()[0][self.asks()[0].index("--question") + 1]
        self.assertIn("omp login anthropic", question)
        self.assertIn("2 live omp process(es)", question)
        self.assertEqual(len(self.alerts()), 1, "the box-health role hears it too")
        self.assertEqual(FakeEnvoy.instances[-1].published[0][0], WATCH.BOX_HEALTH_ROLE_TOPIC)

    def test_one_enabled_row_is_silence(self) -> None:
        db = self.store("up.db", [("anthropic", CAUSE), ("anthropic", None)])
        self.live = {SESSIONS[0]: 101}
        WATCH.run_once(options(self.tmp, db))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.alerts(), [])

    def test_an_mcp_server_credential_is_not_a_model_provider(self) -> None:
        db = self.store(
            "mcp.db",
            [("mcp_oauth:profile:default:https://mcp.slack.com/mcp", "replaced by newer credential"),
             ("anthropic", None)],
        )
        WATCH.run_once(options(self.tmp, db))
        self.assertEqual(self.calls, [], "an expired MCP login is not a lost model provider")


class CrossingOnTranscriptErrors(WatchCase):
    """A provider whose credential still looks enabled, while the sessions cannot use it."""

    def arrange(self, failing: int) -> Path:
        db = self.store("live.db", [("anthropic", None)])
        self.live = {session: 100 + index for index, session in enumerate(SESSIONS)}
        for index, session in enumerate(SESSIONS):
            transcript(
                self.sessions_root / "-src-project",
                session,
                2 if index < failing else 0,
                "No API key for provider: anthropic",
            )
        return db

    def test_under_the_threshold_is_silence(self) -> None:
        db = self.arrange(failing=2)
        WATCH.run_once(options(self.tmp, db, sessions=3))
        self.assertEqual(self.calls, [])

    def test_at_the_threshold_alerts(self) -> None:
        db = self.arrange(failing=3)
        WATCH.run_once(options(self.tmp, db, sessions=3))
        self.assertEqual(len(self.asks()), 1)
        question = self.asks()[0][self.asks()[0].index("--question") + 1]
        self.assertIn("3 live session(s) report an auth error for anthropic", question)

    def test_an_error_older_than_the_window_does_not_count(self) -> None:
        db = self.store("old.db", [("anthropic", None)])
        self.live = {session: 100 for session in SESSIONS[:3]}
        for session in SESSIONS[:3]:
            transcript(
                self.sessions_root / "-src-project",
                session,
                4,
                "No API key for provider: anthropic",
                age=120.0,
            )
        WATCH.run_once(options(self.tmp, db, sessions=3, window=60.0))
        self.assertEqual(self.calls, [])

    def test_talking_about_the_outage_is_not_an_outage(self) -> None:
        """The words travel through transcripts as ordinary text; only an errorMessage counts."""
        db = self.store("chat.db", [("anthropic", None)])
        self.live = {session: 100 for session in SESSIONS[:3]}
        for session in SESSIONS[:3]:
            path = self.sessions_root / "-src-project"
            path.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            (path / f"2026-11-01T12-00-00-000Z_{session}.jsonl").write_text(
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": f"{stamp}.000Z",
                        "message": {
                            "role": "assistant",
                            "content": "The outage was No API key for provider: anthropic, invalid_grant.",
                        },
                    }
                )
                + "\n"
            )
        WATCH.run_once(options(self.tmp, db, sessions=3))
        self.assertEqual(self.calls, [])


class NeverReadsTheCredential(WatchCase):
    def test_no_query_names_the_data_column(self) -> None:
        self.assertNotIn("data", WATCH.CREDENTIAL_COLUMNS)
        self.assertNotIn("data", WATCH.CREDENTIAL_QUERY)

    def test_the_token_never_reaches_an_alert(self) -> None:
        db = self.store("secret.db", [("anthropic", CAUSE)])
        self.live = {SESSIONS[0]: 101}
        WATCH.run_once(options(self.tmp, db))
        everything = json.dumps(self.calls) + json.dumps(self.alerts())
        everything += "".join(path.read_text() for path in (self.tmp / "reports").glob("*.txt"))
        self.assertNotIn(SECRET, everything)
        self.assertNotIn("sk-ant", everything)

    def test_a_token_in_a_cause_is_redacted(self) -> None:
        db = self.store("leak.db", [("anthropic", f"refresh failed with {SECRET} rejected")])
        WATCH.run_once(options(self.tmp, db))
        everything = json.dumps(self.calls) + json.dumps(self.alerts())
        self.assertNotIn(SECRET, everything)
        self.assertIn("<redacted>", everything)

    def test_the_store_is_opened_read_only(self) -> None:
        db = self.store("ro.db", [("anthropic", CAUSE)])
        rows = WATCH.credentials(db)
        self.assertEqual(len(rows), 1)
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute("UPDATE auth_credentials SET provider = 'x'")
        connection.close()


class OncePerCrossing(WatchCase):
    def setUp(self) -> None:
        super().setUp()
        self.db = self.store("down.db", [("anthropic", CAUSE)])
        self.live = {SESSIONS[0]: 101}

    def test_a_standing_outage_is_said_once(self) -> None:
        WATCH.run_once(options(self.tmp, self.db))
        WATCH.run_once(options(self.tmp, self.db))
        WATCH.run_once(options(self.tmp, self.db))
        self.assertEqual(len(self.asks()), 1, "three runs, one ask")

    def test_a_standing_outage_is_not_said_again_on_a_timer(self) -> None:
        """It used to be asked and said again every 30 min with nothing new."""
        WATCH.run_once(options(self.tmp, self.db))
        state = json.loads((self.tmp / "state.json").read_text())
        state["open"]["anthropic"]["told"] = time.time() - 3 * 3600
        (self.tmp / "state.json").write_text(json.dumps(state))
        WATCH.run_once(options(self.tmp, self.db))
        self.assertEqual(len(self.asks()), 1)
        self.assertEqual(self.alerts(), [], "no Envoy message either")

    def test_a_new_fact_is_said_again(self) -> None:
        WATCH.run_once(options(self.tmp, self.db))
        worse = self.store("worse.db", [("anthropic", CAUSE), ("anthropic", CAUSE)])
        WATCH.run_once(options(self.tmp, worse, sessions_store=str(self.db)))
        self.assertEqual(len(self.asks()), 2)
        question = self.asks()[1][self.asks()[1].index("--question") + 1]
        self.assertIn("still have no anthropic login", question)
        self.assertIn("New since the last ask: 1 more credential(s) disabled.", question)
        self.assertEqual(len(self.alerts()), 1)
        self.assertTrue(self.alerts()[0].startswith("LOGIN STILL LOST on "))
        WATCH.run_once(options(self.tmp, worse, sessions_store=str(self.db)))
        self.assertEqual(len(self.asks()), 2, "the same fact again is not news")

    def test_a_login_clears_it_and_resolves_the_ask(self) -> None:
        WATCH.run_once(options(self.tmp, self.db))
        healthy = self.store("up.db", [("anthropic", CAUSE), ("anthropic", None)])
        WATCH.run_once(options(self.tmp, healthy, sessions_store=str(self.db)))
        resolved = [call for call in self.calls if call and call[0] == "resolve-ask"]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0][1:4], ["--ask", "11111111-2222-4333-8444-555555555555", "--kind"])
        self.assertIn("works again", resolved[0][-1])
        self.assertEqual(json.loads((self.tmp / "state.json").read_text())["open"], {})

    def test_a_refused_ask_falls_back_to_a_message_on_the_issue(self) -> None:
        self.answer = (False, "403 ASK_REFUSED")
        WATCH.run_once(options(self.tmp, self.db))
        self.assertEqual([call[0] for call in self.calls], ["ask", "message"])

    def test_dry_run_sends_nothing(self) -> None:
        WATCH.run_once(options(self.tmp, self.db, dry_run=True))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.alerts(), [])


class EveryAskOfACrossingIsClosed(WatchCase):
    """Measured live on 2026-11-01T22:25Z against dispatch://SMOKE-69: the repeat opened ask
    fdfdc332-0732-405d-b6bd-a49fd1d7cade, the clear resolved only the first ask
    78ebd484-b747-4760-9bc3-611cc44f4511, and the repeat's ask stayed open. A four-hour outage
    at the default repeat leaves seven of them behind."""

    def setUp(self) -> None:
        super().setUp()
        self.db = self.store("down.db", [("anthropic", CAUSE)])
        self.live = {SESSIONS[0]: 101}

    def a_new_fact(self) -> Path:
        """A second credential of the provider disabled: the fact a repeat ask now needs."""
        return self.store("worse.db", [("anthropic", CAUSE), ("anthropic", CAUSE)])

    def test_a_repeat_ask_is_recorded_beside_the_first(self) -> None:
        WATCH.run_once(options(self.tmp, self.db))
        WATCH.run_once(options(self.tmp, self.a_new_fact(), sessions_store=str(self.db)))
        record = json.loads((self.tmp / "state.json").read_text())["open"]["anthropic"]
        self.assertEqual(record["asks"], ASK_IDS[:2])
        self.assertEqual(record["ask"], ASK_IDS[0], "the previous format still names the first ask")

    def test_the_clear_resolves_every_ask_the_crossing_opened(self) -> None:
        WATCH.run_once(options(self.tmp, self.db))
        WATCH.run_once(options(self.tmp, self.a_new_fact(), sessions_store=str(self.db)))
        healthy = self.store("up.db", [("anthropic", CAUSE), ("anthropic", None)])
        WATCH.run_once(options(self.tmp, healthy, sessions_store=str(self.db)))
        resolved = [call[2] for call in self.calls if call and call[0] == "resolve-ask"]
        self.assertEqual(resolved, ASK_IDS[:2], "both asks are closed, not just the first")
        self.assertEqual(json.loads((self.tmp / "state.json").read_text())["open"], {})

    def test_a_crossing_opened_by_the_previous_version_keeps_its_ask(self) -> None:
        """The format moves under a live crossing: `ask` alone is what the old code wrote."""
        # Every key the previous version wrote, and `ask` rather than `asks`.
        (self.tmp / "state.json").write_text(
            json.dumps(
                {
                    "open": {
                        "anthropic": {
                            "provider": "anthropic",
                            "db": str(self.db),
                            "credentials": ["  credential 1 (email:sami@theorem.dev), disabled …"],
                            "sessions_failing": 1,
                            "errors": 0,
                            "sessions_affected": 1,
                            "sessions_elsewhere": 0,
                            "window": 300.0,
                            "reason": "every credential disabled",
                            "ask": ASK_IDS[4],
                            "opened": time.time() - 7200,
                            "told": time.time() - 1801,
                        }
                    }
                }
            )
        )
        WATCH.run_once(options(self.tmp, self.db))
        record = json.loads((self.tmp / "state.json").read_text())["open"]["anthropic"]
        self.assertEqual(record["ask"], ASK_IDS[4], "what it last said is taken as told")
        self.assertEqual(self.asks(), [])
        healthy = self.store("up.db", [("anthropic", None)])
        WATCH.run_once(options(self.tmp, healthy, sessions_store=str(self.db)))
        resolved = [call[2] for call in self.calls if call and call[0] == "resolve-ask"]
        self.assertEqual(resolved, [ASK_IDS[4]])


class WhenNobodyCanBeTold(WatchCase):
    """The three paths that used to end in a journal line nobody reads."""

    def setUp(self) -> None:
        super().setUp()
        self.db = self.store("down.db", [("anthropic", CAUSE)])
        self.live = {SESSIONS[0]: 101}

    def test_an_ask_and_its_fallback_message_both_failing_is_said_to_box_health(self) -> None:
        self.answer = (False, "403 ASK_REFUSED")
        WATCH.run_once(options(self.tmp, self.db))
        self.assertEqual([call[0] for call in self.calls], ["ask", "message"])
        failures = [text for text in self.alerts() if "NOBODY WAS TOLD" in text]
        self.assertEqual(len(failures), 1, "the box-health role hears that the human path is down")
        self.assertIn("403 ASK_REFUSED", failures[0])
        self.assertIn("omp login anthropic", failures[0])
        self.assertEqual(len(self.alerts()), 2, "the alert itself still goes out beside it")

    def test_an_identity_the_cli_refuses_is_said_too(self) -> None:
        self.answer = (False, "no DISPATCH_SESSION_ID for the dispatch CLI")
        WATCH.run_once(options(self.tmp, self.db))
        failures = [text for text in self.alerts() if "NOBODY WAS TOLD" in text]
        self.assertEqual(len(failures), 1)
        self.assertIn("no DISPATCH_SESSION_ID", failures[0])

    def test_a_new_fact_that_cannot_be_delivered_is_said_too(self) -> None:
        WATCH.run_once(options(self.tmp, self.db))
        worse = self.store("worse.db", [("anthropic", CAUSE), ("anthropic", CAUSE)])
        self.answer = (False, "500 DOC_SERVICE_UNAVAILABLE")
        WATCH.run_once(options(self.tmp, worse, sessions_store=str(self.db)))
        failures = [text for text in self.every_alert() if "NOBODY WAS TOLD" in text]
        self.assertEqual(len(failures), 1)

    def test_a_failed_ask_is_tried_again_without_saying_it_again(self) -> None:
        self.answer = (False, "500 DOC_SERVICE_UNAVAILABLE")
        WATCH.run_once(options(self.tmp, self.db))
        self.assertEqual(len(self.every_alert()), 2, "NOBODY WAS TOLD and the alert")
        WATCH.run_once(options(self.tmp, self.db))
        self.assertEqual(len(self.asks()), 1, "inside --repeat nothing is tried")
        state = json.loads((self.tmp / "state.json").read_text())
        state["open"]["anthropic"]["told"] = time.time() - 1801
        (self.tmp / "state.json").write_text(json.dumps(state))
        WATCH.run_once(options(self.tmp, self.db))
        self.assertEqual(len(self.asks()), 2, "the ask is tried again")
        self.assertEqual(len(self.every_alert()), 2, "a second failure of the same ask is no news")
        state = json.loads((self.tmp / "state.json").read_text())
        state["open"]["anthropic"]["told"] = time.time() - 1801
        (self.tmp / "state.json").write_text(json.dumps(state))
        self.answer = None
        WATCH.run_once(options(self.tmp, self.db))
        self.assertEqual(len(self.asks()), 3)
        news = self.every_alert()[2:]
        self.assertEqual(len(news), 1)
        self.assertTrue(news[0].startswith("PERSON ASKED: "))
        WATCH.run_once(options(self.tmp, self.db))
        state = json.loads((self.tmp / "state.json").read_text())
        state["open"]["anthropic"]["told"] = time.time() - 1801
        (self.tmp / "state.json").write_text(json.dumps(state))
        WATCH.run_once(options(self.tmp, self.db))
        self.assertEqual(len(self.asks()), 3, "a delivered ask is not repeated")

    def test_a_state_file_that_cannot_be_opened_is_said_and_exits_non_zero(self) -> None:
        blocked = self.tmp / "blocked"
        blocked.write_text("a file where the state directory would be")
        with self.assertRaises(SystemExit) as refused:
            WATCH.run_once(options(self.tmp, self.db, state=str(blocked / "state.json")))
        self.assertEqual(refused.exception.code, 1, "the unit's OnFailure handler needs a failure")
        failures = [text for text in self.alerts() if "cannot open its state file" in text]
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.calls, [], "nothing was sent to a person, which is what it says")

    def test_a_state_file_that_stays_unopenable_is_said_once(self) -> None:
        blocked = self.tmp / "blocked"
        blocked.write_text("a file where the state directory would be")
        for _ in range(3):
            with self.assertRaises(SystemExit):
                WATCH.run_once(options(self.tmp, self.db, state=str(blocked / "state.json")))
        failures = [text for text in self.every_alert() if "cannot open its state file" in text]
        self.assertEqual(len(failures), 1, "every 60 s the same fact: said once")
        self.report_of(failures[0])


class WhenTheWatchItselfDoesNotRun(WatchCase):
    def test_report_failure_names_the_unit_to_box_health(self) -> None:
        for name, value in {
            "MONITOR_UNIT": "omp-login-watch.service",
            "MONITOR_SERVICE_RESULT": "exit-code",
            "MONITOR_EXIT_CODE": "exited",
            "MONITOR_EXIT_STATUS": "1",
        }.items():
            os.environ[name] = value
            self.addCleanup(os.environ.pop, name, None)
        self.assertEqual(WATCH.report_failure(options(self.tmp, self.tmp / "unused.db")), 0)
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn("omp-login-watch.service FAILED", self.alerts()[0])
        self.assertIn("exit-code", self.alerts()[0])
        self.assertEqual(self.calls, [], "the failure report needs no dispatch CLI")


class AFailedPassIsReportedOnlyWhenTheWatchIsLate(WatchCase):
    """One slow pass under IO pressure is not news; 300 s with no completed pass is."""

    def setUp(self) -> None:
        super().setUp()
        self.now = time.time()
        self.heartbeat = self.tmp / "heartbeat"
        self.restore["time"] = WATCH.time
        clock = self

        class Clock:
            def __getattr__(self, name):
                return getattr(time, name)

            def time(self):
                return clock.now

        WATCH.time = Clock()

    def good_pass(self, ago: float) -> None:
        self.heartbeat.write_text("")
        os.utime(self.heartbeat, (self.now - ago, self.now - ago))

    def failed_pass(self) -> list[str]:
        before = len(self.every_alert())
        WATCH.report_failure(options(self.tmp, self.tmp / "unused.db"))
        return self.every_alert()[before:]

    def test_a_completed_pass_writes_the_heartbeat(self) -> None:
        db = self.tmp / "agent.db"
        credential_store(db, [("anthropic", None)])
        WATCH.run_once(options(self.tmp, db))
        self.assertTrue(self.heartbeat.exists())

    def test_a_failure_60_s_after_a_good_pass_sends_nothing(self) -> None:
        self.good_pass(60)
        self.assertEqual(self.failed_pass(), [])

    def test_one_failure_episode_is_one_message(self) -> None:
        """2026-11-02 10:24-10:37Z: seven killed passes in a row sent eight identical messages."""
        self.good_pass(400)
        sent = self.failed_pass()
        self.assertEqual(len(sent), 1)
        self.assertIn("no pass completed for 6 min 40 s", sent[0])
        for _ in range(8):
            self.now += 120
            self.assertEqual(self.failed_pass(), [], "a killed pass every 2 min is the same episode")
        self.now += 3 * 3600
        self.assertEqual(self.failed_pass(), [], "hours on, with no pass completed, still the same")

    def test_a_completed_pass_ends_the_episode(self) -> None:
        self.good_pass(400)
        self.assertEqual(len(self.failed_pass()), 1)
        self.now += 600
        self.good_pass(0)
        self.now += 400
        self.assertEqual(len(self.failed_pass()), 1, "a failure after a completed pass is new")

    def test_no_heartbeat_at_all_sends(self) -> None:
        sent = self.failed_pass()
        self.assertEqual(len(sent), 1)
        self.assertIn("no completed pass on record", sent[0])


class EveryEnvoyMessage(WatchCase):
    """At most 600 characters, ending with the report file that holds the whole text."""

    def test_every_message_is_at_most_600_chars_and_names_its_report(self) -> None:
        cause = CAUSE + " " + "x" * 900
        db = self.store("many.db", [("anthropic", cause)] * 6)
        self.live = {session: 100 + index for index, session in enumerate(SESSIONS)}
        self.answer = (False, "500 " + "E" * 500)
        WATCH.run_once(options(self.tmp, db))
        state = json.loads((self.tmp / "state.json").read_text())
        state["open"]["anthropic"]["told"] = time.time() - 1801
        (self.tmp / "state.json").write_text(json.dumps(state))
        self.answer = None
        WATCH.run_once(options(self.tmp, db))
        worse = self.store("more.db", [("anthropic", cause)] * 7)
        WATCH.run_once(options(self.tmp, worse, sessions_store=str(db)))
        healthy = self.store("ok.db", [("anthropic", None)])
        WATCH.run_once(options(self.tmp, healthy, sessions_store=str(db)))
        blocked = self.tmp / "blocked"
        blocked.write_text("a file where the state directory would be")
        with self.assertRaises(SystemExit):
            WATCH.run_once(options(self.tmp, db, state=str(blocked / "state.json")))
        stale = time.time() - 400
        os.utime(self.tmp / "heartbeat", (stale, stale))
        WATCH.report_failure(options(self.tmp, db))
        sent = self.every_alert()
        for start in (
            "NOBODY WAS TOLD",
            "LOGIN LOST",
            "PERSON ASKED",
            "LOGIN STILL LOST",
            "The anthropic login on",
            "omp-login-watch cannot open its state file",
            "omp-login-watch.service FAILED",
        ):
            self.assertEqual(sum(1 for text in sent if text.startswith(start)), 1, start)
        self.assertEqual(len(sent), 7)
        for text in sent:
            self.report_of(text)
        self.assertIn("x" * 900, self.report_of(sent[1]), "the whole cause is in the report")


class FindingTheTranscripts(WatchCase):
    """The walk of ~/.omp/agent/sessions (1,655 directories) was the step that killed passes in
    the IO storms of 2026-11-02/03; a session's transcript is looked for there once."""

    def setUp(self) -> None:
        super().setUp()
        # A file, not a list: a pass reads the transcripts in a forked child (`bounded`), and
        # only a write outside the process comes back from there.
        self.walk_record = self.tmp / "walks.jsonl"
        self.walk_record.touch()
        real = WATCH.walk_for
        self.restore["walk_for"] = real

        def counted(wanted, deadline):
            with open(self.walk_record, "a") as record:
                record.write(json.dumps(sorted(wanted)) + "\n")
            return real(wanted, deadline)

        WATCH.walk_for = counted

    @property
    def walks(self) -> list[set]:
        return [set(json.loads(line)) for line in self.walk_record.read_text().splitlines()]

    def test_a_transcript_found_once_is_not_walked_for_again(self) -> None:
        db = self.store("live.db", [("anthropic", None)])
        self.live = {session: 100 + index for index, session in enumerate(SESSIONS)}
        for index, session in enumerate(SESSIONS):
            transcript(
                self.sessions_root / "-src-project",
                session,
                2 if index < 3 else 0,
                "No API key for provider: anthropic",
            )
        WATCH.run_once(options(self.tmp, db))
        self.assertEqual(len(self.asks()), 1, "three sessions report errors: a crossing")
        self.assertEqual(self.walks, [set(SESSIONS)])
        WATCH.run_once(options(self.tmp, db))
        self.assertEqual(len(self.walks), 1, "the second pass reads the cache, not the tree")
        self.assertIn("anthropic", json.loads((self.tmp / "state.json").read_text())["open"])
        cache = json.loads((self.tmp / "transcripts.json").read_text())
        self.assertEqual(sorted(cache["paths"]), sorted(SESSIONS))

    def test_a_session_the_walk_did_not_find_waits_10_min(self) -> None:
        db = self.store("live.db", [("anthropic", None)])
        self.live = {SESSIONS[0]: 100}
        WATCH.run_once(options(self.tmp, db))
        WATCH.run_once(options(self.tmp, db))
        self.assertEqual(len(self.walks), 1)
        cache = json.loads((self.tmp / "transcripts.json").read_text())
        cache["missed"][SESSIONS[0]] = time.time() - 601
        (self.tmp / "transcripts.json").write_text(json.dumps(cache))
        WATCH.run_once(options(self.tmp, db))
        self.assertEqual(len(self.walks), 2)

    def test_the_walk_stops_at_its_deadline(self) -> None:
        transcript(self.sessions_root / "-src-project", SESSIONS[0], 0, "x")
        self.assertEqual(WATCH.walk_for({SESSIONS[0]}, time.monotonic() - 1), ({}, False))

    def test_the_transcript_an_omp_holds_open_needs_no_walk(self) -> None:
        path = transcript(self.sessions_root / "-src-held", SESSIONS[0], 0, "x")
        with path.open():
            self.assertEqual(WATCH.held_transcript(os.getpid(), SESSIONS[0]), str(path.resolve()))
            db = self.store("live.db", [("anthropic", None)])
            self.live = {SESSIONS[0]: os.getpid()}
            logged: list[str] = []
            WATCH.log = logged.append
            WATCH.run_once(options(self.tmp, db))
        self.assertEqual(self.walks, [])
        summary = [line for line in logged if "transcripts:" in line][0]
        self.assertIn("1 held open", summary)
        self.assertRegex(summary, r"\(store [0-9.]+s, processes [0-9.]+s, envoy [0-9.]+s, transcripts")


class TheUnitSaysWhenItDidNotRun(unittest.TestCase):
    """systemd is the only thing that sees a pass die; it must hand that to the Envoy path."""

    def test_the_service_triggers_the_failure_report(self) -> None:
        service = (UNITS / "omp-login-watch.service").read_text()
        self.assertIn("OnFailure=omp-login-watch-failed.service", service)
        handler = (UNITS / "omp-login-watch-failed.service").read_text()
        self.assertIn("--report-failure", handler)


class WhichStoreASessionReads(unittest.TestCase):
    """A --profile session has its own credential store and does not lose the shared login."""

    def test_the_default_session_reads_the_shared_store(self) -> None:
        self.assertEqual(WATCH.store_of(["omp", "--resume", "x"], {}), WATCH.DEFAULT_DB)

    def test_a_profile_session_reads_its_own(self) -> None:
        expected = Path.home() / ".omp/profiles/pbc/agent/agent.db"
        self.assertEqual(WATCH.store_of(["omp", "--profile", "pbc"], {}), expected)
        self.assertEqual(WATCH.store_of(["omp", "--profile=pbc"], {}), expected)

    def test_pi_coding_agent_dir_moves_the_store(self) -> None:
        self.assertEqual(
            WATCH.store_of(["omp"], {"PI_CODING_AGENT_DIR": "/tmp/elsewhere"}),
            Path("/tmp/elsewhere/agent/agent.db"),
        )


class Stalled(Exception):
    """The pass was still blocked in a stalled read when the safety net fired."""


class AStalledReadEndsThePassOnTime(WatchCase):
    """The pass started 09:40:33Z on 2026-11-03 was killed at the unit's 120 s having logged
    nothing (dispatch://INFRA-672): its evidence checks a clock between two reads, and a read the
    box has stalled never comes back to look. Each evidence step now runs in a child under a hard
    limit; a cut step is one line, the pass decides nothing, and it exits on time.

    A stalled read is played by a FIFO nobody writes: opening it for reading blocks as a stalled
    read blocks, and no clock checked inside the step can end it. The SIGALRM safety net only
    lets the script before this change -- which blocks in the pass itself -- fail here instead
    of hanging the suite."""

    STALL = 30

    def setUp(self) -> None:
        super().setUp()
        self.logged: list[str] = []
        WATCH.log = self.logged.append
        for name, value in (("PROCESS_SECONDS", 2.0), ("EVIDENCE_SECONDS", 4.0)):
            self.restore[name] = getattr(WATCH, name, None)
            setattr(WATCH, name, value)
        self.fifo = self.tmp / "stalled"
        os.mkfifo(self.fifo)

        def stalled(signum, frame):
            raise Stalled(f"the pass was still blocked in a stalled read after {self.STALL} s")

        previous = signal.signal(signal.SIGALRM, stalled)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        self.addCleanup(signal.alarm, 0)

    def stall(self, store) -> tuple:
        with open(self.fifo, "rb") as handle:
            handle.read()
        return {}, 0, 0

    def run_pass(self, db: Path, **overrides) -> float:
        began = time.monotonic()
        signal.alarm(self.STALL)
        try:
            WATCH.run_once(options(self.tmp, db, **overrides))
        except Stalled as error:
            self.fail(f"{error}; it logged: {self.logged}")
        finally:
            signal.alarm(0)
        return time.monotonic() - began

    def test_a_finished_store_read_opens_the_crossing_when_the_process_scan_is_cut(self) -> None:
        """Every row of the provider disabled is the store's own fact: it opens the crossing on its
        own, and the ask says the session evidence was not read."""
        db = self.store("down.db", [("anthropic", CAUSE)])
        self.live = {SESSIONS[0]: 101}
        WATCH.omp_sessions = self.stall

        took = self.run_pass(db)

        self.assertLess(took, 2.0 + 6.0, self.logged)
        self.assertTrue(
            any(line.startswith("  the process scan: cut after 2 s") for line in self.logged), self.logged
        )
        self.assertEqual(len(self.asks()), 1, "the person is asked on the store's fact alone")
        question = self.asks()[0][self.asks()[0].index("--question") + 1]
        self.assertIn("Session evidence was not read this pass (the process scan cut", question)
        self.assertNotIn("0 live omp process(es)", question)
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn("session evidence not read this pass", self.alerts()[0])
        self.assertFalse((self.tmp / "heartbeat").exists(), "a pass that read no sessions did not complete")

        WATCH.omp_sessions = lambda store: (dict(self.live), 0, 0)
        self.run_pass(db)
        self.assertEqual(len(self.asks()), 1, "the next full pass repeats nothing")
        self.assertEqual(json.loads((self.tmp / "state.json").read_text())["open"]["anthropic"]["unread"], "")

    def test_a_stalled_process_read_with_every_login_working_opens_nothing(self) -> None:
        db = self.store("up.db", [("anthropic", None)])
        WATCH.omp_sessions = self.stall

        took = self.run_pass(db)

        self.assertLess(took, 2.0 + 6.0, self.logged)
        self.assertTrue(any(line.startswith("evidence cut (the process scan)") for line in self.logged))
        self.assertEqual(self.asks(), [])
        self.assertEqual(self.every_alert(), [])
        self.assertFalse((self.tmp / "heartbeat").exists(), "nor says a pass completed")

    def test_a_stalled_transcript_tail_does_not_clear_an_open_crossing(self) -> None:
        db = self.store("live.db", [("anthropic", None)])
        self.live = {session: 100 + index for index, session in enumerate(SESSIONS[:3])}
        paths = [
            transcript(self.sessions_root / "-src-project", session, 2, "No API key for provider: anthropic")
            for session in SESSIONS[:3]
        ]
        self.run_pass(db)
        self.assertEqual(len(self.asks()), 1)
        paths[0].unlink()
        os.mkfifo(paths[0])

        took = self.run_pass(db)

        self.assertLess(took, 4.0 + 2.0 + 6.0, self.logged)
        self.assertTrue(any(line.startswith("evidence cut (the transcript tails)") for line in self.logged))
        self.assertNotIn("resolve-ask", [call[0] for call in self.calls])
        self.assertIn("anthropic", json.loads((self.tmp / "state.json").read_text())["open"])

    def test_the_evidence_budget_counts_from_the_process_start(self) -> None:
        """62.9 s of start-up on 2026-11-03 07:37:56Z: the budget is what is left after it."""
        db = self.store("down.db", [("anthropic", CAUSE)])
        self.live = {SESSIONS[0]: 101}
        WATCH.process_started = lambda: time.time() - 3.8

        self.run_pass(db)

        self.assertTrue(
            any(line.startswith("  the credential store: not started") for line in self.logged), self.logged
        )
        self.assertEqual(self.asks(), [])


class APartialReadClearsNothing(WatchCase):
    """A walk stopped at the budget leaves transcripts unread; they can only hide errors, so
    quiet is not established and an open crossing stays open."""

    def test_a_walk_stopped_at_its_budget_holds_the_clear(self) -> None:
        db = self.store("live.db", [("anthropic", None)])
        self.live = {session: 100 + index for index, session in enumerate(SESSIONS[:3])}
        for session in SESSIONS[:3]:
            transcript(self.sessions_root / "-src-project", session, 2, "No API key for provider: anthropic")
        WATCH.run_once(options(self.tmp, db))
        self.assertEqual(len(self.asks()), 1)
        (self.tmp / "transcripts.json").unlink()
        self.restore.setdefault("walk_for", WATCH.walk_for)
        WATCH.walk_for = lambda wanted, deadline: ({}, False)

        WATCH.run_once(options(self.tmp, db))

        self.assertNotIn("resolve-ask", [call[0] for call in self.calls])
        self.assertIn("anthropic", json.loads((self.tmp / "state.json").read_text())["open"])


if __name__ == "__main__":
    unittest.main()
