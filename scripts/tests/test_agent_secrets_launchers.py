#!/usr/bin/env python3
"""scripts/agent-secrets-session, and the agent launchers that exec their agent through it.

On a host with the agent-secrets helper's unit installed, the session execs the agent under
`agent-secrets register --exec --`, keeping the pid, and adds `--wait 10` only while `agent-secrets
launcher login-status` prints `issued`. It registers nothing when `login-status` prints no state
(the helper does not answer, or the client cannot run), when agent-secrets is missing, in a box
(AGENT_SECRETS_KEY_DIR) or on a machine without the helper; the agent starts either way.

Technique: stub `agent-secrets` (login-status answers as the case needs; `register` prints the
flags and environment it got, then execs the command after `--`) and the agents themselves,
first on PATH.
"""

from __future__ import annotations

import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SESSION = DOTFILES / "scripts" / "agent-secrets-session"
BROKER_URL = next(
    line.split("=", 1)[1].strip()
    for line in (DOTFILES / "agent-secrets" / "broker.env").read_text().splitlines()
    if line.startswith("AGENT_SECRETS_URL=")
)


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


AGENT_SECRETS_STUB = r"""
case "$1" in
    launcher)
        echo login-status >>"$STUB_LOG"
        # The first call after a pin bump: mise's auto-install progress on stderr, before the state.
        [[ -n "${STUB_AUTOINSTALL:-}" ]] && echo "mise agent-secrets@legion-envoy-v9.9.9 [1/3] download agent-secrets-amd64.tar.gz" >&2
        case "${STUB_LOGIN_STATE:-none}" in
            issued) echo issued; exit 0 ;;
            unreachable) echo "agent-secrets launcher login-status: dial unix $AGENT_SECRETS_HELPER_SOCK: connect: no such file or directory" >&2; exit 1 ;;
            broken) echo "mise ERROR Tool not installed for shim: agent-secrets" >&2; exit 1 ;;
            # A failed auto-install: progress first, the cause last, nothing on stdout.
            installfail) echo "mise agent-secrets@legion-envoy-v9.9.9 [1/3] download agent-secrets-amd64.tar.gz" >&2; echo "mise ERROR Failed to install github:sjawhar/legion@legion-envoy-v9.9.9: checksum mismatch" >&2; exit 1 ;;
            # The legion #1589 client prints the state on stdout and what to do about it on stderr
            # (cmd/agent-secrets/main.go, cmdLauncherLoginStatus, at 3d5f07e5).
            pending) echo pending; echo "agent-secrets launcher login-status: a machine login is waiting for approval (code ABCD-EFGH)" >&2; exit 1 ;;
            none) echo none; echo "agent-secrets launcher login-status: no machine login has run on this helper; run: agent-secrets launcher login" >&2; exit 1 ;;
            *) echo "$STUB_LOGIN_STATE"; echo "agent-secrets launcher login-status: the last machine login is $STUB_LOGIN_STATE; run: agent-secrets launcher login" >&2; exit 1 ;;
        esac
        ;;
    register)
        shift
        flags=()
        while [[ "$1" != -- ]]; do flags+=("$1"); shift; done
        shift
        echo "REGISTER ${flags[*]} SOCK=${AGENT_SECRETS_HELPER_SOCK:-unset} URL=${AGENT_SECRETS_URL:-unset}"
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


class AgentSecretsSession(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        (self.home / ".config").mkdir(parents=True)
        self.stub_dir = self.root / "bin"
        self.stub_dir.mkdir()
        self.log = self.root / "agent-secrets.log"
        self.sock = self.root / "run" / "agent-secrets" / "helper.sock"
        write_stub(self.stub_dir, "agent-secrets", AGENT_SECRETS_STUB)
        # The agent reports its pid: registration must keep the session's.
        write_stub(self.stub_dir, "agent", 'echo "AGENT pid=$$ $*"')
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

    def session(self, **extra: str) -> tuple[subprocess.CompletedProcess[str], int]:
        proc = subprocess.Popen(
            [str(SESSION), "agent", "--flag"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**self.env, **extra},
        )
        out, err = proc.communicate(timeout=60)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err), proc.pid

    def status_calls(self) -> int:
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0

    def test_credential_issued_registers_and_waits_keeping_the_pid(self) -> None:
        self.install_helper_unit()
        for extra in ({}, {"STUB_AUTOINSTALL": "1"}):
            with self.subTest(**extra):
                result, pid = self.session(STUB_LOGIN_STATE="issued", **extra)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout.splitlines(),
                    [
                        f"REGISTER --wait 10 --exec SOCK={self.sock} URL={BROKER_URL}",
                        f"AGENT pid={pid} --flag",
                    ],
                )
                self.assertEqual(result.stderr, "")

    def test_caller_socket_and_url_are_kept(self) -> None:
        self.install_helper_unit()
        result, _ = self.session(
            STUB_LOGIN_STATE="issued",
            AGENT_SECRETS_HELPER_SOCK="/elsewhere/helper.sock",
            AGENT_SECRETS_URL="https://b.example",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines()[0],
            "REGISTER --wait 10 --exec SOCK=/elsewhere/helper.sock URL=https://b.example",
        )

    def test_no_credential_registers_without_waiting(self) -> None:
        """Every state but issued; stderr around the state (auto-install, #1589's remedy) aside."""
        self.install_helper_unit()
        for state in ("none", "pending", "denied", "expired"):
            for extra in ({}, {"STUB_AUTOINSTALL": "1"}):
                with self.subTest(state=state, **extra):
                    result, pid = self.session(STUB_LOGIN_STATE=state, **extra)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        result.stdout.splitlines(),
                        [
                            f"REGISTER --exec SOCK={self.sock} URL={BROKER_URL}",
                            f"AGENT pid={pid} --flag",
                        ],
                    )
                    self.assertEqual(result.stderr, "")

    def test_no_state_launches_unregistered_and_says_why(self) -> None:
        """A stopped helper, or a client that cannot run, costs the launch nothing."""
        self.install_helper_unit()
        for state, cause in (
            ("unreachable", "connect: no such file or directory"),
            ("broken", "Tool not installed for shim: agent-secrets"),
            ("installfail", "Failed to install github:sjawhar/legion@legion-envoy-v9.9.9"),
        ):
            with self.subTest(state=state):
                result, pid = self.session(STUB_LOGIN_STATE=state)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [f"AGENT pid={pid} --flag"])
                self.assertIn(cause, result.stderr)
                self.assertNotIn("[1/3] download", result.stderr)

    def test_missing_client_launches_unregistered(self) -> None:
        self.install_helper_unit()
        (self.stub_dir / "agent-secrets").unlink()
        result, pid = self.session()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [f"AGENT pid={pid} --flag"])
        self.assertIn("agent-secrets is not on PATH", result.stderr)

    def test_box_or_machine_without_the_helper_never_asks(self) -> None:
        cases = {
            "no helper unit": {},
            "box": {"AGENT_SECRETS_KEY_DIR": str(self.root / "keys")},
        }
        for name, extra in cases.items():
            with self.subTest(case=name):
                if name == "box":
                    self.install_helper_unit()
                result, pid = self.session(STUB_LOGIN_STATE="issued", **extra)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [f"AGENT pid={pid} --flag"])
                self.assertEqual(result.stderr, "")
        self.assertEqual(self.status_calls(), 0)

    def test_usage(self) -> None:
        result = subprocess.run(
            [str(SESSION)], capture_output=True, text=True, env=self.env, check=False, timeout=30
        )
        self.assertEqual(result.returncode, 2)

    def test_each_launcher_registers_its_agent(self) -> None:
        self.install_helper_unit()
        for name, (argv, agent_line) in LAUNCHERS.items():
            with self.subTest(launcher=name):
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    env={**self.env, "STUB_LOGIN_STATE": "issued"},
                    check=False,
                    timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = result.stdout.splitlines()
                self.assertEqual(
                    lines[0], f"REGISTER --wait 10 --exec SOCK={self.sock} URL={BROKER_URL}"
                )
                self.assertTrue(lines[1].startswith(agent_line), result.stdout)


if __name__ == "__main__":
    unittest.main()
