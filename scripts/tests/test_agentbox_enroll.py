#!/usr/bin/env python3
"""scripts/agentbox's box enrollment: every way it ends, as the box's key dir shows it.

`agentbox new` puts enrollment.pending in a box's key dir before `docker run`, and the
agent-secrets client in the box waits on that marker. enroll_box must end it every time:
enrolled (enrollment written, no marker, no error file) or not (enrollment.error written, then
the marker removed), so no client waits on an enrollment that will never come.

Technique: source scripts/agentbox (its source guard runs nothing) with `docker`,
`agent-secrets`, `systemctl`, `mise` and `sleep` stubbed first on PATH, and call enroll_box on
a scratch key dir.
"""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"
THUMBPRINT = "A" * 43


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class AgentboxEnroll(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        (self.home / ".config" / "agent-secrets").mkdir(parents=True)
        (self.home / ".config" / "agent-secrets" / "operator").write_text("sjawhar\n")
        self.stub_dir = self.root / "bin"
        self.stub_dir.mkdir()
        self.calls = self.root / "calls"
        write_stub(self.stub_dir, "mise", "exit 1")
        write_stub(self.stub_dir, "sleep", 'echo sleep >>"$STUB_CALLS"')
        write_stub(self.stub_dir, "systemctl", 'exit "${STUB_HELPER_ACTIVE:-0}"')
        write_stub(
            self.stub_dir,
            "docker",
            'echo "docker $*" >>"$STUB_CALLS"\n'
            'case "${STUB_KEYGEN:-ok}" in\n'
            f'    ok) echo "{THUMBPRINT}" ;;\n'
            '    fail) echo "keygen: no /run/user dir" >&2; exit 1 ;;\n'
            "    empty) ;;\n"
            "esac",
        )
        write_stub(
            self.stub_dir,
            "agent-secrets",
            'case "$1" in\n'
            '    launcher) exit "${STUB_LOGIN_STATUS:-0}" ;;\n'
            "    enroll)\n"
            '        echo "enroll $*" >>"$STUB_CALLS"\n'
            '        case "${STUB_ENROLL:-ok}" in\n'
            '            ok) echo enr-123 >"$AGENT_SECRETS_KEY_DIR/enrollment" ;;\n'
            '            fail) echo "broker unreachable" >&2; exit 1 ;;\n'
            "            nofile) ;;\n"
            "        esac ;;\n"
            '    *) echo "unexpected agent-secrets $*" >&2; exit 99 ;;\n'
            "esac",
        )
        self.run_base = self.root / "run"
        self.keydir = self.run_base / "box1" / "run-user" / "agent-secrets"
        self.keydir.mkdir(parents=True)
        (self.keydir / "enrollment.pending").write_text("")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def enroll(self, **stubs: str) -> subprocess.CompletedProcess[str]:
        script = (
            f'source "{AGENTBOX}"\n'
            f'RUN_BASE="{self.run_base}"\n'
            'if enroll_box box1 sid-1; then echo rc=0; else echo "rc=$?"; fi\n'
        )
        env = {
            "HOME": str(self.home),
            "PATH": f"{self.stub_dir}:/usr/bin:/bin",
            "DOTFILES_DIR": str(DOTFILES),
            "STUB_CALLS": str(self.calls),
            **stubs,
        }
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, env=env, check=False, timeout=60
        )

    def files(self) -> set[str]:
        return {p.name for p in self.keydir.iterdir()}

    def calls_matching(self, prefix: str) -> list[str]:
        if not self.calls.exists():
            return []
        return [line for line in self.calls.read_text().splitlines() if line.startswith(prefix)]

    def error(self) -> str:
        return (self.keydir / "enrollment.error").read_text()

    def test_enrolled_leaves_only_the_enrollment(self) -> None:
        result = self.enroll()
        self.assertIn("rc=0", result.stdout, result.stderr)
        self.assertEqual(self.files(), {"enrollment"})
        (enroll,) = self.calls_matching("enroll ")
        self.assertIn(f"--thumbprint {THUMBPRINT}", enroll)
        self.assertIn("--session-id sid-1", enroll)

    def test_gives_up_after_ten_attempts(self) -> None:
        result = self.enroll(STUB_ENROLL="fail")
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assertNotIn("enrollment.pending", self.files())
        self.assertNotIn("enrollment", self.files())
        self.assertIn("failed 10 times", self.error())
        self.assertIn("broker unreachable", self.error())
        self.assertEqual(len(self.calls_matching("enroll ")), 10)
        self.assertEqual(len(self.calls_matching("sleep")), 9)

    def test_keygen_failure(self) -> None:
        result = self.enroll(STUB_KEYGEN="fail")
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assertNotIn("enrollment.pending", self.files())
        self.assertIn("keygen failed", self.error())
        self.assertIn("no /run/user dir", self.error())
        self.assertEqual(self.calls_matching("enroll "), [])

    def test_keygen_without_thumbprint(self) -> None:
        result = self.enroll(STUB_KEYGEN="empty")
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assertNotIn("enrollment.pending", self.files())
        self.assertIn("printed no thumbprint", self.error())
        self.assertEqual(self.calls_matching("enroll "), [])

    def test_enroll_that_writes_no_enrollment(self) -> None:
        result = self.enroll(STUB_ENROLL="nofile")
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assertNotIn("enrollment.pending", self.files())
        self.assertIn("wrote no", self.error())

    def test_no_launcher_credential_never_touches_the_box(self) -> None:
        result = self.enroll(STUB_LOGIN_STATUS="1")
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assertNotIn("enrollment.pending", self.files())
        self.assertIn("agent-secrets-login <github-login>", self.error())
        self.assertEqual(self.calls_matching("docker "), [])

    def test_broker_ready_names_the_fix(self) -> None:
        cases = {
            "no operator": ({}, True, "agent-secrets-login <github-login>"),
            "helper down": (
                {"STUB_HELPER_ACTIVE": "3"},
                False,
                "systemctl --user status agent-secrets-helper",
            ),
            "no credential": (
                {"STUB_LOGIN_STATUS": "1"},
                False,
                "agent-secrets-login <github-login>",
            ),
        }
        operator = self.home / ".config" / "agent-secrets" / "operator"
        for name, (stubs, drop_operator, fix) in cases.items():
            with self.subTest(case=name):
                operator.write_text("" if drop_operator else "sjawhar\n")
                script = (
                    f'source "{AGENTBOX}"\n'
                    'if broker_ready; then echo ready; else echo "unready: $BROKER_UNREADY"; fi\n'
                )
                env = {"HOME": str(self.home), "PATH": f"{self.stub_dir}:/usr/bin:/bin", **stubs}
                result = subprocess.run(
                    ["bash", "-c", script], capture_output=True, text=True, env=env, check=False
                )
                self.assertIn("unready:", result.stdout, result.stderr)
                self.assertIn(fix, result.stdout)


if __name__ == "__main__":
    unittest.main()
