#!/usr/bin/env python3
"""scripts/agent-secrets-name-session: a Claude Code session names itself to the secrets helper.

Claude Code's SessionStart hook (.claude/settings.json) runs it with the hook's JSON on stdin. It
runs `agent-secrets register --session-id <session_id>` only after `agent-secrets identity` exits
0, which on a host means the caller is inside a session the helper registered
(dispatch://INFRA-3848). It does nothing in an agent box, with no helper socket, or with no
session id, and a refusal writes one line on stderr and exits 0: a hook never stops the start.
The hook is registered for the sources startup, resume and clear and not compact, so it fires
once for each session id in a process; SessionStart does not fire for a subagent.

Technique: a stub `agent-secrets` first on PATH appends each argv to a file and exits as the test
says, and a listening Unix socket stands in for the helper's.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "scripts" / "agent-secrets-name-session"
SETTINGS = DOTFILES / ".claude" / "settings.json"
SESSION = "6f1c2b7e-1d1a-4c55-9a8e-0b2d3f4a5c6d"
STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
case "$1" in
identity) exit "${FAKE_IDENTITY_EXIT:-0}" ;;
register) [ -n "$FAKE_REGISTER_ERR" ] && printf '%s\\n' "$FAKE_REGISTER_ERR" >&2; exit "${FAKE_REGISTER_EXIT:-0}" ;;
esac
"""


class NameSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ns-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)], check=False))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        stub = self.bin / "agent-secrets"
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        self.calls = self.tmp / "calls"
        self.sock = self.tmp / "h.sock"
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(self.sock))
        self.listener.listen()
        self.addCleanup(self.listener.close)

    def run_hook(self, payload: dict, **env: str) -> subprocess.CompletedProcess[str]:
        base = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "CALLS": str(self.calls),
            "AGENT_SECRETS_HELPER_SOCK": str(self.sock),
            "XDG_RUNTIME_DIR": str(self.tmp / "run"),
        }
        base.update(env)
        return subprocess.run(
            [str(SCRIPT)], input=json.dumps(payload), env=base, capture_output=True, text=True, timeout=30
        )

    def logged(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_names_the_session_to_the_helper(self) -> None:
        done = self.run_hook({"session_id": SESSION, "hook_event_name": "SessionStart", "source": "startup"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.logged(), ["identity", f"register --session-id {SESSION}"])

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
