#!/usr/bin/env python3
"""scripts/agent-secrets-name-session: a Claude Code session names itself to the secrets helper.

Claude Code's async SessionStart hook (.claude/settings.json) runs it with the hook's JSON on
stdin. It runs `agent-secrets register --session-id <session_id>` only after `agent-secrets
identity` exits 0 (dispatch://INFRA-3848), and not when `agent-secrets-helper sessions` shows the
nearest session root above it named already: a claude process names one id, and `/clear` gives it
another. It does nothing in an agent box, with no helper socket, or with no session id. A refusal
writes one line on stderr and exits 0. Claude owns the background process and collects its output
without delaying session start; the script keeps its parent link. The hook is registered for the
sources startup, resume and clear and not compact; SessionStart does not fire for a subagent.

Technique: assert the native async setting, then keep the real script as a child while the fake
helper delays each call. The fake client logs argv and returns controlled status, the fake
`agent-secrets-helper sessions` prints the rows a test gives it, with this test process standing
where claude does as the hook's parent, and a listening Unix socket stands in for the helper's.
Hung calls must still end at the script's own deadlines.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "scripts" / "agent-secrets-name-session"
SETTINGS = DOTFILES / ".claude" / "settings.json"
SESSION = "6f1c2b7e-1d1a-4c55-9a8e-0b2d3f4a5c6d"
FIRST = "0c9d8e7f-6a5b-4c3d-8e2f-1a0b9c8d7e6f"
STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
case "$1" in
identity) sleep "${FAKE_IDENTITY_DELAY:-0}"; exit "${FAKE_IDENTITY_EXIT:-0}" ;;
register)
    sleep "${FAKE_REGISTER_DELAY:-0}"
    [ -n "$FAKE_REGISTER_ERR" ] && printf '%s\\n' "$FAKE_REGISTER_ERR" >&2
    [ -n "${NAMED:-}" ] && touch "$NAMED"
    exit "${FAKE_REGISTER_EXIT:-0}"
    ;;
esac
"""
# `agent-secrets-helper sessions` prints FAKE_SESSIONS, the helper's rows (root pid, runtime id,
# enrollment id, state), and exits FAKE_SESSIONS_EXIT.
HELPER_STUB = """#!/bin/sh
[ "$1" = sessions ] || exit 2
sleep "${FAKE_SESSIONS_DELAY:-0}"
[ -n "${FAKE_SESSIONS:-}" ] && printf '%s\\n' "$FAKE_SESSIONS"
exit "${FAKE_SESSIONS_EXIT:-0}"
"""


def session_row(pid: int, runtime_id: str) -> str:
    return f"{pid}\t{runtime_id}\t7d8bb9de-bea6-4c08-ad54-0470028a348a\tenrolled"


# The session the launcher registered: this test process stands where claude does, the parent of
# the hook, and no register has named it yet.
UNNAMED_ROOT = session_row(os.getpid(), f"host:{os.getpid()}:138752288")


class NameSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ns-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)], check=False))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, text in (("agent-secrets", STUB), ("agent-secrets-helper", HELPER_STUB)):
            stub = self.bin / name
            stub.write_text(text)
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        self.calls = self.tmp / "calls"
        self.sock = self.tmp / "h.sock"
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(self.sock))
        self.listener.listen()
        self.addCleanup(self.listener.close)

    def hook_env(self, **env: str) -> dict[str, str]:
        base = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "CALLS": str(self.calls),
            "AGENT_SECRETS_HELPER_SOCK": str(self.sock),
            "XDG_RUNTIME_DIR": str(self.tmp / "run"),
            "FAKE_SESSIONS": UNNAMED_ROOT,
        }
        base.update(env)
        return base

    def run_hook(self, payload: dict, timeout: float = 30, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT)], input=json.dumps(payload), env=self.hook_env(**env),
            capture_output=True, text=True, timeout=timeout,
        )

    def logged(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_names_the_session_to_the_helper(self) -> None:
        done = self.run_hook({"session_id": SESSION, "hook_event_name": "SessionStart", "source": "startup"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.logged(), ["identity", f"register --session-id {SESSION}"])

    def test_async_session_start_names_after_the_slow_helper_answers(self) -> None:
        hooks = json.loads(SETTINGS.read_text())["hooks"]["SessionStart"]
        hook = next(hook for entry in hooks for hook in entry["hooks"]
                    if hook.get("command") == "agent-secrets-name-session")
        self.assertIs(hook.get("async"), True, "session start waits for the helper")
        for delayed_call in ("IDENTITY", "REGISTER"):
            with self.subTest(delayed_call=delayed_call):
                self.calls.unlink(missing_ok=True)
                named = self.tmp / f"named-{delayed_call}"
                with subprocess.Popen(
                    [str(SCRIPT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True,
                    env=self.hook_env(NAMED=str(named), **{f"FAKE_{delayed_call}_DELAY": "2"}),
                ) as process:
                    # Claude owns the async process; keep that parent link here too.
                    with self.assertRaises(subprocess.TimeoutExpired):
                        process.communicate(json.dumps({"session_id": SESSION, "source": "startup"}), timeout=0.5)
                    self.assertFalse(named.exists(), "the delayed helper has already finished naming")
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 0, stderr)
                self.assertEqual((stdout, stderr), ("", ""))
                self.assertTrue(named.exists(), "the background hook never named the session")
                self.assertEqual(self.logged(), ["identity", f"register --session-id {SESSION}"])

    def test_each_helper_call_keeps_its_own_deadline(self) -> None:
        for delayed_call in ("IDENTITY", "SESSIONS", "REGISTER"):
            with self.subTest(delayed_call=delayed_call):
                self.calls.unlink(missing_ok=True)
                started = time.monotonic()
                done = self.run_hook(
                    {"session_id": SESSION, "source": "startup"}, timeout=13,
                    **{f"FAKE_{delayed_call}_DELAY": "30"},
                )
                self.assertLess(time.monotonic() - started, 13)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout, "")
                expected = ["identity"]
                if delayed_call != "IDENTITY":
                    # A sessions read that hangs is no evidence of a name, so the hook registers.
                    expected.append(f"register --session-id {SESSION}")
                if delayed_call == "REGISTER":
                    self.assertEqual(len(done.stderr.splitlines()), 1, done.stderr)
                    self.assertIn(f"register --session-id {SESSION} failed", done.stderr)
                else:
                    self.assertEqual(done.stderr, "")
                self.assertEqual(self.logged(), expected)

    def test_finds_the_helpers_default_socket(self) -> None:
        run = self.tmp / "run" / "agent-secrets"
        run.mkdir(parents=True)
        other = socket.socket(socket.AF_UNIX)
        other.bind(str(run / "helper.sock"))
        other.listen()
        self.addCleanup(other.close)
        env = {"AGENT_SECRETS_HELPER_SOCK": ""}
        self.run_hook({"session_id": SESSION, "source": "startup"}, **env)
        self.assertEqual(self.logged(), ["identity", f"register --session-id {SESSION}"])

    def test_does_not_register_outside_a_registered_session(self) -> None:
        self.run_hook({"session_id": SESSION, "source": "startup"}, FAKE_IDENTITY_EXIT="1")
        self.assertEqual(self.logged(), ["identity"])

    def test_a_clear_in_a_process_already_named_does_not_name_it_again(self) -> None:
        # /clear gives this claude process a new id and runs the hook again; the helper named its
        # session FIRST at its start, and refuses it any other id.
        refusal = f"agent-secrets register: BAD_REQUEST: this process is in session {FIRST}, which cannot become session {SESSION}"
        done = self.run_hook(
            {"session_id": SESSION, "source": "clear"},
            FAKE_SESSIONS=session_row(os.getpid(), f"session-{FIRST}"),
            FAKE_REGISTER_EXIT="1", FAKE_REGISTER_ERR=refusal,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((done.stdout, done.stderr), ("", ""))
        self.assertEqual(self.logged(), ["identity"])

    def test_names_a_new_process_whatever_other_sessions_are_named(self) -> None:
        # Control: a process whose own session has no name yet is named, even when a session that
        # is not its own (a pid that is no ancestor of the hook) has one.
        rows = "\n".join([session_row(4194303, f"session-{FIRST}"), UNNAMED_ROOT])
        done = self.run_hook({"session_id": SESSION, "source": "startup"}, FAKE_SESSIONS=rows)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.logged(), ["identity", f"register --session-id {SESSION}"])

    def test_registers_when_the_sessions_cannot_be_read(self) -> None:
        # An unread list is no evidence of a name: the hook registers, as it did before the read.
        for name, env in {
            "the helper refuses the list": {"FAKE_SESSIONS": "", "FAKE_SESSIONS_EXIT": "2"},
            "the list names no root above the hook": {"FAKE_SESSIONS": session_row(4194303, f"session-{FIRST}")},
        }.items():
            with self.subTest(name):
                self.calls.unlink(missing_ok=True)
                done = self.run_hook({"session_id": SESSION, "source": "clear"}, **env)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(self.logged(), ["identity", f"register --session-id {SESSION}"])

    def test_does_nothing_in_a_box_with_no_socket_or_with_no_id(self) -> None:
        cases = {
            "a box": ({"session_id": SESSION}, {"AGENTBOX_BOX": "agentbox-0123456789ab"}),
            "no socket": ({"session_id": SESSION}, {"AGENT_SECRETS_HELPER_SOCK": str(self.tmp / "absent.sock")}),
            "a plain file": ({"session_id": SESSION}, {"AGENT_SECRETS_HELPER_SOCK": str(self.calls.parent / "plain")}),
            "no session id": ({"source": "startup"}, {}),
            "input that is not JSON": (None, {}),
        }
        (self.tmp / "plain").write_text("")
        for name, (payload, env) in cases.items():
            with self.subTest(name):
                # Each case reads its own calls, so one case's calls never fail another's.
                self.calls.unlink(missing_ok=True)
                base = {
                    "PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp), "CALLS": str(self.calls),
                    "AGENT_SECRETS_HELPER_SOCK": str(self.sock), "XDG_RUNTIME_DIR": str(self.tmp / "run"),
                }
                base.update(env)
                done = subprocess.run(
                    [str(SCRIPT)], input="not json" if payload is None else json.dumps(payload),
                    env=base, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(self.logged(), [])

    def test_says_why_when_the_helper_refuses_the_name_and_does_not_fail_the_start(self) -> None:
        refusal = f"agent-secrets register: BAD_REQUEST: this process is in session other, which cannot become session {SESSION}"
        done = self.run_hook({"session_id": SESSION, "source": "resume"}, FAKE_REGISTER_EXIT="1", FAKE_REGISTER_ERR=refusal)
        self.assertEqual(done.returncode, 0)
        lines = [line for line in done.stderr.splitlines() if "agent-secrets-name-session:" in line]
        self.assertEqual(len(lines), 1, done.stderr)
        self.assertIn(SESSION, lines[0])
        self.assertIn(refusal, lines[0])


class HookRegistrationTest(unittest.TestCase):
    """The hook runs the script on SessionStart for a new or resumed id, never on compact."""

    def test_session_start_runs_the_script_once_per_id(self) -> None:
        hooks = json.loads(SETTINGS.read_text()).get("hooks", {})
        entries = hooks.get("SessionStart", [])
        named = [
            entry for entry in entries
            if any(h.get("command") == "agent-secrets-name-session" for h in entry.get("hooks", []))
        ]
        self.assertEqual(len(named), 1, f"SessionStart hooks: {entries}")
        self.assertEqual(sorted(named[0].get("matcher", "").split("|")), ["clear", "resume", "startup"])
        for event in ("SubagentStart", "SubagentStop", "PreCompact"):
            self.assertNotIn("agent-secrets-name-session", json.dumps(hooks.get(event, [])))

    def test_the_script_is_executable(self) -> None:
        self.assertTrue(os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable")


if __name__ == "__main__":
    unittest.main()
