#!/usr/bin/env python3
"""scripts/agent-secrets-session, and the agent launchers that exec their agent through it.

On a host with the agent-secrets helper's unit installed, the session execs the agent under
`agent-secrets register --exec --`, keeping the pid, and adds `--wait 10` only while `agent-secrets
machine login-status` prints `issued`. It registers nothing when `login-status` prints no state
(the helper does not answer, or the client cannot run), when agent-secrets is missing, in a box
(AGENT_SECRETS_KEY_DIR) or on a machine without the helper; the agent starts either way.

scripts/agent-secrets-login names the operator by the email they sign in to Dispatch with: a GitHub
login is refused (exit 2) before the operator file is written.

Every consumer of agent-secrets/broker.env (agent-secrets-session, agent-secrets-login, .bashrc,
scripts/agentbox; scripts/secret-run in test_secret_run.py) ends with the same AGENT_SECRETS_URL,
by agent-secrets/broker-url.sh: broker.env's, or env.local's over it, exported with its record
DOTFILES_AGENT_SECRETS_URL, when the caller has no URL or has the one a dotfiles load recorded (a
stale default); a URL the caller chose itself stays.

Technique: stub `agent-secrets` (login-status answers as the case needs; `register` prints the
flags and environment it got, then execs the command after `--`), the agents themselves and, for
the login, `systemctl`, first on PATH.
"""

from __future__ import annotations

import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SESSION = DOTFILES / "scripts" / "agent-secrets-session"
LOGIN = DOTFILES / "scripts" / "agent-secrets-login"
HELPER_WRAPPER = DOTFILES / "agent-secrets" / "agent-secrets-helper"
BROKER_URL = next(
    line.split("=", 1)[1].strip()
    for line in (DOTFILES / "agent-secrets" / "broker.env").read_text().splitlines()
    if line.startswith("AGENT_SECRETS_URL=")
)
BASHRC = DOTFILES / ".bashrc"
AGENTBOX = DOTFILES / "scripts" / "agentbox"
STALE_URL = "https://stale.example"
CHOSEN_URL = "http://127.0.0.1:8443"
LOCAL_URL = "https://local.example"
# case -> (the caller's environment, env.local's URL or None, the URL and record the consumer ends
# with; "unset" is no record). A stale default is a URL a dotfiles load recorded before broker.env
# changed. A chosen URL is one exported from outside the dotfiles (Legion's dev-broker.sh prints
# one for a local broker), also in a shell whose own load recorded the default.
URL_CASES: dict[str, tuple[dict[str, str], str | None, tuple[str, str]]] = {
    "unset": ({}, None, (BROKER_URL, BROKER_URL)),
    "stale default": (
        {"AGENT_SECRETS_URL": STALE_URL, "DOTFILES_AGENT_SECRETS_URL": STALE_URL},
        None,
        (BROKER_URL, BROKER_URL),
    ),
    "chosen URL": ({"AGENT_SECRETS_URL": CHOSEN_URL}, None, (CHOSEN_URL, "unset")),
    "chosen URL over a recorded default": (
        {"AGENT_SECRETS_URL": CHOSEN_URL, "DOTFILES_AGENT_SECRETS_URL": BROKER_URL},
        None,
        (CHOSEN_URL, BROKER_URL),
    ),
    "env.local": ({}, LOCAL_URL, (LOCAL_URL, LOCAL_URL)),
    "env.local over a stale default": (
        {"AGENT_SECRETS_URL": STALE_URL, "DOTFILES_AGENT_SECRETS_URL": STALE_URL},
        LOCAL_URL,
        (LOCAL_URL, LOCAL_URL),
    ),
}


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def write_env_local(home: Path, url: str | None) -> None:
    """~/.config/agent-secrets/env.local naming url, or no env.local at all."""
    path = home / ".config" / "agent-secrets" / "env.local"
    if url is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"AGENT_SECRETS_URL={url}\n", encoding="utf-8")


AGENT_SECRETS_STUB = r"""
case "$1" in
    machine)
        if [[ "$2" == login ]]; then
            echo "LOGIN URL=${AGENT_SECRETS_URL:-unset} MARK=${DOTFILES_AGENT_SECRETS_URL:-unset}"
            exit 0
        fi
        echo login-status >>"$STUB_LOG"
        # The first call after a pin bump: mise's auto-install progress on stderr, before the state.
        [[ -n "${STUB_AUTOINSTALL:-}" ]] && echo "mise agent-secrets@legion-envoy-v9.9.9 [1/3] download agent-secrets-amd64.tar.gz" >&2
        case "${STUB_LOGIN_STATE:-none}" in
            issued) echo issued; exit 0 ;;
            unreachable) echo "agent-secrets machine login-status: dial unix $AGENT_SECRETS_HELPER_SOCK: connect: no such file or directory" >&2; exit 1 ;;
            broken) echo "mise ERROR Tool not installed for shim: agent-secrets" >&2; exit 1 ;;
            # A failed auto-install: progress first, the cause last, nothing on stdout.
            installfail) echo "mise agent-secrets@legion-envoy-v9.9.9 [1/3] download agent-secrets-amd64.tar.gz" >&2; echo "mise ERROR Failed to install github:sjawhar/legion@legion-envoy-v9.9.9: checksum mismatch" >&2; exit 1 ;;
            # The legion-envoy-v9.0.0 client prints the state on stdout and what to do about it on
            # stderr (cmd/agent-secrets/machine.go, cmdMachineLoginStatus, at 011b572a).
            pending) echo pending; echo "agent-secrets machine login-status: a machine login is waiting for approval (code ABCD-EFGH)" >&2; exit 1 ;;
            none) echo none; echo "agent-secrets machine login-status: no machine login has run on this helper; run: agent-secrets machine login" >&2; exit 1 ;;
            *) echo "$STUB_LOGIN_STATE"; echo "agent-secrets machine login-status: the last machine login is $STUB_LOGIN_STATE; run: agent-secrets machine login" >&2; exit 1 ;;
        esac
        ;;
    register)
        shift
        flags=()
        while [[ "$1" != -- ]]; do flags+=("$1"); shift; done
        shift
        echo "REGISTER ${flags[*]} SOCK=${AGENT_SECRETS_HELPER_SOCK:-unset} URL=${AGENT_SECRETS_URL:-unset} MARK=${DOTFILES_AGENT_SECRETS_URL:-unset}"
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
        # shims/omp gets the pinned release's install dir on PATH from `mise env -s bash`; cld
        # execs its agent by name.
        tool_dir = self.home / ".mise" / "installs" / "github-sjawhar-oh-my-pi" / "1.0.0" / "bin"
        tool_dir.mkdir(parents=True)
        write_stub(tool_dir, "omp", 'echo "AGENT omp $*"')
        write_stub(self.stub_dir, "mise", f"echo 'export PATH=\"{tool_dir}:$PATH\"'")
        write_stub(self.stub_dir, "claude", 'echo "AGENT claude"')
        self.env = {
            "DOTFILES_DIR": str(DOTFILES),
            "HOME": str(self.home),
            "PATH": f"{self.stub_dir}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_RUNTIME_DIR": str(self.root / "run"),
            "STUB_LOG": str(self.log),
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
                        f"REGISTER --wait 10 --exec SOCK={self.sock} URL={BROKER_URL} MARK={BROKER_URL}",
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
            "REGISTER --wait 10 --exec SOCK=/elsewhere/helper.sock URL=https://b.example MARK=unset",
        )

    def test_url_follows_broker_env_unless_the_caller_chose_one(self) -> None:
        self.install_helper_unit()
        for case, (caller, local, (url, mark)) in URL_CASES.items():
            with self.subTest(case=case):
                write_env_local(self.home, local)
                result, _ = self.session(STUB_LOGIN_STATE="issued", **caller)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout.splitlines()[0],
                    f"REGISTER --wait 10 --exec SOCK={self.sock} URL={url} MARK={mark}",
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
                            f"REGISTER --exec SOCK={self.sock} URL={BROKER_URL} MARK={BROKER_URL}",
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

    def test_login_refuses_a_github_login_before_writing_the_operator(self) -> None:
        """With the client and the helper's unit both answering, only the email check stands
        between a GitHub login and the operator file."""
        write_stub(self.stub_dir, "systemctl", "exit 0")
        result = subprocess.run(
            [str(LOGIN), "sjawhar"],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("'sjawhar' is not an email", result.stderr)
        self.assertFalse((self.home / ".config" / "agent-secrets" / "operator").exists())

    def test_login_url_follows_broker_env_unless_the_caller_chose_one(self) -> None:
        """The login reaches `agent-secrets machine login` once the helper's socket is there."""
        write_stub(self.stub_dir, "systemctl", "exit 0")
        self.sock.parent.mkdir(parents=True)
        listener = socket.socket(socket.AF_UNIX)
        self.addCleanup(listener.close)
        listener.bind(str(self.sock))
        for case, (caller, local, (url, mark)) in URL_CASES.items():
            with self.subTest(case=case):
                write_env_local(self.home, local)
                result = subprocess.run(
                    [str(LOGIN), "operator@example.com"],
                    capture_output=True,
                    text=True,
                    env={**self.env, **caller},
                    check=False,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [f"LOGIN URL={url} MARK={mark}"])

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
                    lines[0],
                    f"REGISTER --wait 10 --exec SOCK={self.sock} URL={BROKER_URL} MARK={BROKER_URL}",
                )
                self.assertTrue(lines[1].startswith(agent_line), result.stdout)


class SourcedBrokerUrl(unittest.TestCase):
    """.bashrc, which every shell sources, and scripts/agentbox, sourced as its own tests source it:
    a child of the sourcing shell gets the URL and its record."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.home = root / "home"
        (self.home / ".config").mkdir(parents=True)
        # A DOTFILES_DIR holding only the real agent-secrets/, so .bashrc runs none of the
        # checkout's tools (bin/mise, dockerfunc).
        self.dotfiles = root / "dotfiles"
        self.dotfiles.mkdir()
        (self.dotfiles / "agent-secrets").symlink_to(DOTFILES / "agent-secrets")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def child_sees(self, path: Path, caller: dict[str, str]) -> str:
        script = (
            'source "$1"\n'
            "exec sh -c 'echo \"URL=${AGENT_SECRETS_URL-unset} MARK=${DOTFILES_AGENT_SECRETS_URL-unset}\"'"
        )
        result = subprocess.run(
            ["bash", "--norc", "-c", script, "--", str(path)],
            capture_output=True,
            text=True,
            env={
                "HOME": str(self.home),
                "PATH": "/usr/bin:/bin",
                "DOTFILES_DIR": str(self.dotfiles),
                **caller,
            },
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()[-1]

    def test_url_follows_broker_env_unless_the_caller_chose_one(self) -> None:
        for path in (BASHRC, AGENTBOX):
            for case, (caller, local, (url, mark)) in URL_CASES.items():
                with self.subTest(file=path.name, case=case):
                    write_env_local(self.home, local)
                    self.assertEqual(self.child_sees(path, caller), f"URL={url} MARK={mark}")


class HelperWrapper(unittest.TestCase):
    """agent-secrets/agent-secrets-helper, the unit's ExecStart: `serve` with no arguments, and any
    other subcommand passed through. A wrapper that always ran `serve` turned `agent-secrets-helper
    sessions` into a second helper that took the socket and re-pinned every live session."""

    def run_wrapper(self, *args: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            write_stub(root / "bin", "mise", 'shift 3; echo "HELPER $*"')
            result = subprocess.run(
                [str(HELPER_WRAPPER), *args],
                capture_output=True,
                text=True,
                env={"DOTFILES_DIR": str(root), "HOME": str(root), "PATH": "/usr/bin:/bin"},
                check=True,
                timeout=30,
            )
        return result.stdout.strip()

    def test_no_arguments_serves(self) -> None:
        self.assertEqual(self.run_wrapper(), "HELPER agent-secrets-helper serve")

    def test_a_subcommand_is_passed_through(self) -> None:
        self.assertEqual(self.run_wrapper("sessions"), "HELPER agent-secrets-helper sessions")


if __name__ == "__main__":
    unittest.main()
