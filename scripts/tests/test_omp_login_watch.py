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
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

# The script under test, and the unit files beside it. Both are overridable, because
# ~/.dotfiles is production with no staging step: a change is tested from a copy outside the
# checkout (OMP_LOGIN_WATCH_SCRIPT=<copy> OMP_LOGIN_WATCH_UNITS=<dir> python3 this file) and
# saved into the checkout only once it passes.
SCRIPT = Path(os.environ.get("OMP_LOGIN_WATCH_SCRIPT") or Path(__file__).resolve().parents[1] / "omp-login-watch")
UNITS = Path(os.environ.get("OMP_LOGIN_WATCH_UNITS") or Path(__file__).resolve().parents[2] / "omp")
LOADER = importlib.machinery.SourceFileLoader("omp_login_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

SECRET = "sk-ant-oat01-THIS-IS-THE-TOKEN-NOBODY-MAY-READ"
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
        WATCH.Envoy = FakeEnvoy
        WATCH.SESSION_ROOT = self.sessions_root
        WATCH.dispatch_call = self.dispatch_call
        WATCH.omp_sessions = lambda store: (dict(self.live), self.unnamed, self.elsewhere)
        WATCH.log = lambda message: None

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

    def test_it_is_said_again_after_the_repeat_window(self) -> None:
        WATCH.run_once(options(self.tmp, self.db))
        state = json.loads((self.tmp / "state.json").read_text())
        state["open"]["anthropic"]["told"] = time.time() - 1801
        (self.tmp / "state.json").write_text(json.dumps(state))
        WATCH.run_once(options(self.tmp, self.db))
        self.assertEqual(len(self.asks()), 2)
        question = self.asks()[1][self.asks()[1].index("--question") + 1]
        self.assertIn("still have no anthropic login", question)

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

    def age_the_crossing(self) -> None:
        state = json.loads((self.tmp / "state.json").read_text())
        state["open"]["anthropic"]["told"] = time.time() - 1801
        (self.tmp / "state.json").write_text(json.dumps(state))

    def test_a_repeat_ask_is_recorded_beside_the_first(self) -> None:
        WATCH.run_once(options(self.tmp, self.db))
        self.age_the_crossing()
        WATCH.run_once(options(self.tmp, self.db))
        record = json.loads((self.tmp / "state.json").read_text())["open"]["anthropic"]
        self.assertEqual(record["asks"], ASK_IDS[:2])
        self.assertEqual(record["ask"], ASK_IDS[0], "the previous format still names the first ask")

    def test_the_clear_resolves_every_ask_the_crossing_opened(self) -> None:
        WATCH.run_once(options(self.tmp, self.db))
        self.age_the_crossing()
        WATCH.run_once(options(self.tmp, self.db))
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
        self.assertEqual(record["asks"], [ASK_IDS[4], ASK_IDS[0]])
        healthy = self.store("up.db", [("anthropic", None)])
        WATCH.run_once(options(self.tmp, healthy, sessions_store=str(self.db)))
        resolved = [call[2] for call in self.calls if call and call[0] == "resolve-ask"]
        self.assertEqual(resolved, [ASK_IDS[4], ASK_IDS[0]])


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

    def test_a_repeat_that_cannot_be_delivered_is_said_too(self) -> None:
        WATCH.run_once(options(self.tmp, self.db))
        state = json.loads((self.tmp / "state.json").read_text())
        state["open"]["anthropic"]["told"] = time.time() - 1801
        (self.tmp / "state.json").write_text(json.dumps(state))
        self.answer = (False, "500 DOC_SERVICE_UNAVAILABLE")
        WATCH.run_once(options(self.tmp, self.db))
        failures = [text for text in self.every_alert() if "NOBODY WAS TOLD" in text]
        self.assertEqual(len(failures), 1)

    def test_a_state_file_that_cannot_be_opened_is_said_and_exits_non_zero(self) -> None:
        blocked = self.tmp / "blocked"
        blocked.write_text("a file where the state directory would be")
        with self.assertRaises(SystemExit) as refused:
            WATCH.run_once(options(self.tmp, self.db, state=str(blocked / "state.json")))
        self.assertEqual(refused.exception.code, 1, "the unit's OnFailure handler needs a failure")
        failures = [text for text in self.alerts() if "cannot open its state file" in text]
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.calls, [], "nothing was sent to a person, which is what it says")


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

    def test_a_failure_400_s_after_sends_once_with_the_gap_then_repeats_at_30_min(self) -> None:
        self.good_pass(400)
        sent = self.failed_pass()
        self.assertEqual(len(sent), 1)
        self.assertIn("no pass completed for 6 min 40 s", sent[0])
        self.now += 120
        self.assertEqual(self.failed_pass(), [], "a second failure 2 min later repeats nothing")
        self.now += 29 * 60
        self.assertEqual(len(self.failed_pass()), 1, "31 min after the first report it says it again")

    def test_no_heartbeat_at_all_sends(self) -> None:
        sent = self.failed_pass()
        self.assertEqual(len(sent), 1)
        self.assertIn("no completed pass on record", sent[0])


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


if __name__ == "__main__":
    unittest.main()
