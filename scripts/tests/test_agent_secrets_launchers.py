#!/usr/bin/env python3
"""The agent launchers (shims/omp, scripts/cld, scripts/oc) and agent-secrets registration.

On a host with the agent-secrets helper installed, each launcher execs its agent under
`agent-secrets register --exec --`, and adds `--wait 10` only while `agent-secrets launcher
login-status` exits 0: without a launcher credential the helper cannot enroll the session, so
the wait would hold every launch for the full 10 s. A box (AGENT_SECRETS_KEY_DIR) and a machine
without the helper never register.

Technique: stub `agent-secrets` (login-status exits as the case needs; `register` prints the
flags it got and execs the command after `--`) and the agent binary itself, first on PATH.
"""

from __future__ import annotations

import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


AGENT_SECRETS_STUB = r"""
case "$1" in
    launcher)
        echo login-status >>"$STUB_LOG"
        exit "${STUB_LOGIN_STATUS:-1}"
        ;;
    register)
        shift
        flags=()
        while [[ "$1" != -- ]]; do flags+=("$1"); shift; done
        shift
        echo "REGISTER ${flags[*]} SOCK=${AGENT_SECRETS_HELPER_SOCK:-unset}"
        exec "$@"
        ;;
esac
echo "unexpected agent-secrets $*" >&2
exit 99
"""


# name -> (argv, the line the agent stub prints once it runs)
LAUNCHERS: dict[str, tuple[list[str], str]] = {
    "omp": ([str(DOTFILES / "shims" / "omp"), "--version"], "AGENT omp --version"),
    "cld": ([str(DOTFILES / "scripts" / "cld"), "--version"], "AGENT claude"),
    "oc": ([sys.executable, str(DOTFILES / "scripts" / "oc"), "--version"], "AGENT opencode"),
}


class LauncherRegistration(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        (self.home / ".config").mkdir(parents=True)
        self.stub_dir = self.root / "bin"
        self.stub_dir.mkdir()
        self.log = self.root / "agent-secrets.log"
        write_stub(self.stub_dir, "agent-secrets", AGENT_SECRETS_STUB)
        # shims/omp reaches omp through `mise x github:sjawhar/oh-my-pi -- omp` when no local
        # build is on PATH; cld and oc exec their agents by name.
        write_stub(self.stub_dir, "mise", 'shift 3; echo "AGENT $*"')
        write_stub(self.stub_dir, "claude", 'echo "AGENT claude"')
        write_stub(self.stub_dir, "opencode", 'echo "AGENT opencode"')
        self.env = {
            "DOTFILES_DIR": str(DOTFILES),
            "HOME": str(self.home),
            "PATH": f"{self.stub_dir}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_RUNTIME_DIR": str(self.root / "run"),
            "STUB_LOG": str(self.log),
            # A seed file already named: no launcher reaches for the real one.
            "NATS_NKEY_SEED_FILE": "/dev/null",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def install_helper_unit(self) -> None:
        unit = self.home / ".config" / "systemd" / "user" / "agent-secrets-helper.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("", encoding="utf-8")

    def launch(self, name: str, **extra: str) -> subprocess.CompletedProcess[str]:
        argv, _ = LAUNCHERS[name]
        return subprocess.run(
            argv, capture_output=True, text=True, env={**self.env, **extra}, check=False, timeout=60
        )

    def status_calls(self) -> int:
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0

    def test_credential_issued_waits_for_enrollment(self) -> None:
        self.install_helper_unit()
        for name, (_, agent_line) in LAUNCHERS.items():
            with self.subTest(launcher=name):
                result = self.launch(name, STUB_LOGIN_STATUS="0")
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = result.stdout.splitlines()
                sock = self.root / "run" / "agent-secrets" / "helper.sock"
                self.assertEqual(lines[0], f"REGISTER --wait 10 --exec SOCK={sock}")
                self.assertTrue(lines[1].startswith(agent_line), result.stdout)

    def test_no_credential_registers_without_waiting(self) -> None:
        self.install_helper_unit()
        for name, (_, agent_line) in LAUNCHERS.items():
            with self.subTest(launcher=name):
                before = self.status_calls()
                result = self.launch(name, STUB_LOGIN_STATUS="1")
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = result.stdout.splitlines()
                self.assertTrue(lines[0].startswith("REGISTER --exec SOCK="), result.stdout)
                self.assertTrue(lines[1].startswith(agent_line), result.stdout)
                self.assertEqual(self.status_calls(), before + 1)

    def test_no_helper_installed_never_registers(self) -> None:
        for name, (_, agent_line) in LAUNCHERS.items():
            with self.subTest(launcher=name):
                result = self.launch(name, STUB_LOGIN_STATUS="0")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.startswith(agent_line), result.stdout)
                self.assertNotIn("REGISTER", result.stdout)
        self.assertEqual(self.status_calls(), 0)

    def test_box_never_registers(self) -> None:
        self.install_helper_unit()
        for name, (_, agent_line) in LAUNCHERS.items():
            with self.subTest(launcher=name):
                result = self.launch(
                    name, STUB_LOGIN_STATUS="0", AGENT_SECRETS_KEY_DIR=str(self.root / "keys")
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.startswith(agent_line), result.stdout)
                self.assertNotIn("REGISTER", result.stdout)
        self.assertEqual(self.status_calls(), 0)

    def test_helper_without_client_still_launches(self) -> None:
        """A helper unit with no agent-secrets on PATH must not turn into a failed launch."""
        self.install_helper_unit()
        (self.stub_dir / "agent-secrets").unlink()
        for name, (_, agent_line) in LAUNCHERS.items():
            with self.subTest(launcher=name):
                result = self.launch(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.startswith(agent_line), result.stdout)
                self.assertIn("agent-secrets is not on PATH", result.stderr)


if __name__ == "__main__":
    unittest.main()
