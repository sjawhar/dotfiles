#!/usr/bin/env python3
"""The local agent-secrets installer installs only when env.local names a broker.

Technique: every subprocess has a private DOTFILES_DIR, HOME, and PATH. Its fixture copies the
helper's unit and wrapper, while stubs first on PATH log systemctl state changes. The tested local
installer is the checkout's own file, so its child installer uses the fixture unit without reaching
the real user systemd manager.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
LOCAL_INSTALLER = DOTFILES / "installers" / "agent-secrets-local.sh"
ROOT_INSTALLER = DOTFILES / "install.sh"
UNIT_NAME = "agent-secrets-helper.service"

SYSTEMCTL_STUB = r'''
echo "systemctl $*" >>"$STUB_LOG"
case "$*" in
    "--user enable --now agent-secrets-helper.service")
        # Starting a stopped helper leaves it booting: it does not answer on its socket yet.
        [[ -f "$STUB_STATE/active" ]] || touch "$STUB_STATE/booting"
        touch "$STUB_STATE/enabled" "$STUB_STATE/active"
        exit "${STUB_ENABLE_EXIT:-0}"
        ;;
    "--user is-enabled --quiet agent-secrets-helper.service")
        test -f "$STUB_STATE/enabled"
        exit
        ;;
    "--user is-active --quiet agent-secrets-helper.service")
        test -f "$STUB_STATE/active"
        exit
        ;;
    "--user try-restart agent-secrets-helper.service")
        touch "$STUB_STATE/active" "$STUB_STATE/booting"
        exit 0
        ;;
    "--user daemon-reload")
        exit 0
        ;;
esac
'''

MISE_STUB = r'''
echo "mise $*" >>"$STUB_LOG"
if [[ "$1" == where && "$2" == agent-secrets ]]; then
    printf '%s\n' "$STUB_MISE_WHERE"
fi
'''

AGENT_SECRETS_STUB = r'''
echo "agent-secrets $*" >>"$STUB_LOG"
exit 1
'''

TIMEOUT_STUB = r'''
echo "timeout $*" >>"$STUB_LOG"
if [[ -n "${STUB_TIMEOUT_EXIT:-}" ]]; then
    exit "$STUB_TIMEOUT_EXIT"
fi
# Stay in the test's process group so its outer watchdog also stops a polling shell.
exec /usr/bin/timeout --foreground "$@"
'''

# A helper that a start or a restart left booting (the systemctl stub's `booting` file) refuses
# `sessions` STUB_REFUSALS times (default 3; `never` keeps refusing), as the real helper refuses
# with exit 2 before its socket listens, and then answers. Each answer adds a `helper answered`
# line to the log after the call's own line.
BOOTING_HELPER_STUB = r'''
echo "helper $*" >>"$STUB_LOG"
if [[ "$1" == sessions && -f "$STUB_STATE/booting" ]]; then
    refused=$(( $(cat "$STUB_STATE/refused" 2>/dev/null || echo 0) + 1 ))
    echo "$refused" >"$STUB_STATE/refused"
    limit="${STUB_REFUSALS:-3}"
    if [[ "$limit" == never || "$refused" -le "$limit" ]]; then
        echo "agent-secrets-helper: not answering at helper.sock" >&2
        exit 2
    fi
    rm -f "$STUB_STATE/booting" "$STUB_STATE/refused"
fi
echo "helper answered $*" >>"$STUB_LOG"
printf "%s" "${STUB_SESSIONS:-}"
'''

# `agent-secrets machine login-status` as cmdMachineLoginStatus answers: the state word on stdout,
# why on stderr, exit 0 only for `issued`. The client dials the helper once, so while the helper
# is still booting (or with STUB_UNREACHABLE set) it fails with the dial error and prints no state.
LOGIN_STATUS_STUB = r'''
echo "agent-secrets $*" >>"$STUB_LOG"
[[ "$*" == "machine login-status" ]] || exit 1
if [[ -f "$STUB_STATE/booting" || -n "${STUB_UNREACHABLE:-}" ]]; then
    echo "agent-secrets machine login-status: dial unix helper.sock: connect: no such file or directory" >&2
    exit 1
fi
printf '%s\n' "${STUB_LOGIN_STATE:-issued}"
if [[ -n "${STUB_LOGIN_REASON:-}" ]]; then
    printf '%s\n' "$STUB_LOGIN_REASON" >&2
fi
exit "${STUB_LOGIN_EXIT:-0}"
'''


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class AgentSecretsLocal(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.fixture = self.root / "fixture"
        self.fixture.mkdir()
        self.home = self.root / "home"
        self.log = self.root / "stub.log"
        self.state = self.root / "state"
        self.state.mkdir()
        self.stub_dir = self.fixture / "bin"
        self.stub_dir.mkdir()
        self.agent_secrets = self.fixture / "agent-secrets"
        self.agent_secrets.mkdir()
        for name in ("agent-secrets-helper.service", "agent-secrets-helper"):
            shutil.copy2(DOTFILES / "agent-secrets" / name, self.agent_secrets / name)
        (self.agent_secrets / "broker.env").write_text(
            "AGENT_SECRETS_URL=https://broker.example.invalid\n", encoding="utf-8"
        )
        write_stub(self.stub_dir, "systemctl", SYSTEMCTL_STUB)
        write_stub(self.stub_dir, "mise", MISE_STUB)
        write_stub(self.stub_dir, "agent-secrets", AGENT_SECRETS_STUB)
        write_stub(self.stub_dir, "jj", 'echo "jj $*" >>"$STUB_LOG"')
        write_stub(self.stub_dir, "gh", 'echo "gh $*" >>"$STUB_LOG"')
        write_stub(self.stub_dir, "timeout", TIMEOUT_STUB)
        self.mise_v1 = self.make_mise_install("legion-envoy-v1.0.0")
        self.env = {
            "PATH": f"{self.stub_dir}:/usr/bin:/bin",
            "HOME": str(self.home),
            "DOTFILES_DIR": str(self.fixture),
            "STUB_LOG": str(self.log),
            "STUB_STATE": str(self.state),
            "STUB_MISE_WHERE": str(self.mise_v1),
            "STUB_SESSIONS": "",
            "AGENT_SECRETS_URL": "https://inherited.example.invalid",
            "DOTFILES_AGENT_SECRETS_URL": "https://inherited.example.invalid",
        }
        systemctl = self.stub_dir / "systemctl"
        self.assertTrue(systemctl.is_file())
        self.assertTrue(os.access(systemctl, os.X_OK))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @property
    def env_local(self) -> Path:
        return self.home / ".config" / "agent-secrets" / "env.local"

    @property
    def unit(self) -> Path:
        return self.home / ".config" / "systemd" / "user" / UNIT_NAME

    @property
    def marker(self) -> Path:
        return self.home / ".config" / "agent-secrets" / "helper.installed"

    def make_mise_install(self, version: str) -> Path:
        install = self.root / "mise" / version
        binary_dir = install / "bin"
        binary_dir.mkdir(parents=True)
        write_stub(
            binary_dir,
            "agent-secrets-helper",
            'echo "helper $*" >>"$STUB_LOG"\nprintf "%s" "${STUB_SESSIONS:-}"\n'
            'exit "${STUB_SESSIONS_EXIT:-0}"',
        )
        return install

    def write_env_local(self, content: str) -> None:
        self.env_local.parent.mkdir(parents=True, exist_ok=True)
        self.env_local.write_text(content, encoding="utf-8")

    def run_local(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(LOCAL_INSTALLER)],
            capture_output=True,
            text=True,
            env={**self.env, **extra},
            check=False,
            timeout=30,
        )

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    @staticmethod
    def systemctl_verb(lines: list[str], verb: str) -> list[str]:
        pattern = re.compile(rf"^systemctl --user {re.escape(verb)}(?: |$)")
        return [line for line in lines if pattern.search(line)]

    def assert_positive_log(self) -> None:
        lines = self.calls()
        self.assertTrue(lines, "the stub log is empty")
        self.assertTrue(
            any(line.startswith("systemctl ") for line in lines),
            f"the systemctl stub at {self.stub_dir / 'systemctl'} was not called: {lines}",
        )

    def assert_skips_helper(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.lexists(self.unit))
        self.assertFalse(any(UNIT_NAME in line for line in self.calls()))
        self.assertIn("sets no AGENT_SECRETS_URL", result.stdout)

    def assert_no_lifecycle_calls(self, lines: list[str]) -> None:
        for verb in ("enable", "start", "restart", "try-restart"):
            self.assertEqual(self.systemctl_verb(lines, verb), [], lines)

    def append_harmless_change(self, path: Path) -> None:
        path.write_text(path.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
        if path.name == "agent-secrets-helper":
            self.assertTrue(os.access(path, os.X_OK))

    def change_pin(self) -> None:
        self.env["STUB_MISE_WHERE"] = str(self.make_mise_install("legion-envoy-v2.0.0"))

    def change_unit(self) -> None:
        self.append_harmless_change(self.agent_secrets / "agent-secrets-helper.service")

    def change_wrapper(self) -> None:
        self.append_harmless_change(self.agent_secrets / "agent-secrets-helper")

    def change_broker_env(self) -> None:
        self.append_harmless_change(self.agent_secrets / "broker.env")

    def assert_restarts_after_change(
        self, change: Callable[[], None], expected_marker_fragment: str | None = None
    ) -> None:
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        first_result = self.run_local()
        self.assertEqual(first_result.returncode, 0, first_result.stderr)
        original_marker = self.marker.read_text(encoding="utf-8")
        before = len(self.calls())
        change()

        result = self.run_local()
        new_calls = self.calls()[before:]

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.systemctl_verb(new_calls, "try-restart"),
            [f"systemctl --user try-restart {UNIT_NAME}"],
        )
        for verb in ("enable", "start", "restart"):
            self.assertEqual(self.systemctl_verb(new_calls, verb), [], new_calls)
        marker = self.marker.read_text(encoding="utf-8")
        self.assertNotEqual(marker, original_marker)
        if expected_marker_fragment is not None:
            self.assertIn(expected_marker_fragment, marker)
        self.assert_positive_log()

    def assert_defers_restart_after_change(self, change: Callable[[], None]) -> None:
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        first_result = self.run_local()
        self.assertEqual(first_result.returncode, 0, first_result.stderr)
        original_marker = self.marker.read_text(encoding="utf-8")
        before = len(self.calls())
        change()
        self.env["STUB_SESSIONS"] = "registered-session\n"

        result = self.run_local()
        new_calls = self.calls()[before:]

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_lifecycle_calls(new_calls)
        self.assertIn("NOT restarted", result.stderr)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), original_marker)
        self.assert_positive_log()

    def assert_keeps_helper_when_sessions_unanswered(
        self, expected_status: str, **stub_env: str
    ) -> list[str]:
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        first_result = self.run_local()
        self.assertEqual(first_result.returncode, 0, first_result.stderr)
        original_marker = self.marker.read_text(encoding="utf-8")
        before = len(self.calls())
        self.change_pin()
        self.env.update(stub_env)

        result = self.run_local()
        new_calls = self.calls()[before:]

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_lifecycle_calls(new_calls)
        self.assertIn("did not answer", result.stderr)
        self.assertIn(expected_status, result.stderr)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), original_marker)
        self.assert_positive_log()
        return new_calls

    def prepare_root_installer(self) -> Path:
        root_installer = self.fixture / "install.sh"
        shutil.copy2(ROOT_INSTALLER, root_installer)
        installers = self.fixture / "installers"
        installers.mkdir()
        installer_text = ROOT_INSTALLER.read_text(encoding="utf-8")
        source_names = re.findall(
            r'source "\$\{DOTFILES_DIR\}/installers/([^"]+)"', installer_text
        )
        command_names = re.findall(
            r'bash "\$\{DOTFILES_DIR\}/installers/([^"]+)"', installer_text
        )
        self.assertIn("secretsd.sh", source_names)
        self.assertIn("knives.sh", source_names)
        for name in source_names + command_names + ["agent-secrets.sh"]:
            target = installers / name
            if name in {"agent-secrets.sh", "agent-secrets-local.sh", "lib.sh"}:
                shutil.copy2(DOTFILES / "installers" / name, target)
            elif name == "knives.sh":
                target.write_text('touch "$(dirname "$STUB_LOG")/knives-ran"\n', encoding="utf-8")
            else:
                target.write_text("# empty root-installer fixture\n", encoding="utf-8")
        shutil.copy2(DOTFILES / "installers" / "lib.sh", installers / "lib.sh")
        return root_installer

    def run_root(self, root_installer: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(root_installer)],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
            timeout=30,
        )

    def test_a_env_local_url_installs_the_helper(self) -> None:
        url = "https://broker.example.invalid"
        self.write_env_local(f"AGENT_SECRETS_URL={url}\n")

        result = self.run_local()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.unit.is_symlink())
        self.assertEqual(
            os.readlink(self.unit), str(self.agent_secrets / "agent-secrets-helper.service")
        )
        self.assertIn(
            f"systemctl --user enable --now {UNIT_NAME}", self.calls()
        )
        # The first run started the helper itself, so it records the installed files without the
        # `sessions` call that decides a restart (`timeout 10 ... sessions`), and with no restart.
        for verb in ("restart", "try-restart"):
            self.assertEqual(self.systemctl_verb(self.calls(), verb), [], self.calls())
        helper = self.mise_v1 / "bin" / "agent-secrets-helper"
        self.assertNotIn(f"timeout 10 {helper} sessions", self.calls())
        self.assertTrue(self.marker.is_file())
        self.assertIn("legion-envoy-v1.0.0", self.marker.read_text(encoding="utf-8"))
        self.assertNotIn(url, result.stdout)
        self.assertNotIn(url, result.stderr)
        self.assert_positive_log()

    def test_b_no_env_local_skips_the_helper(self) -> None:
        self.assert_skips_helper(self.run_local())

    def test_c_env_local_without_url_skips_the_helper(self) -> None:
        self.write_env_local("# a comment\nOTHER=1\n")
        self.assert_skips_helper(self.run_local())

    def test_c2_empty_url_skips_the_helper(self) -> None:
        self.write_env_local("AGENT_SECRETS_URL=\n")
        self.assert_skips_helper(self.run_local())

    def test_d_existing_unit_stays_unchanged_without_env_local(self) -> None:
        self.unit.parent.mkdir(parents=True)
        self.unit.symlink_to(self.agent_secrets / "agent-secrets-helper.service")
        before = os.lstat(self.unit)
        before_target = os.readlink(self.unit)

        result = self.run_local()

        after = os.lstat(self.unit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.assertEqual(os.readlink(self.unit), before_target)
        self.assertFalse(any(UNIT_NAME in line for line in self.calls()))

    def test_e_unchanged_running_second_run_is_quiet(self) -> None:
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        self.assertEqual(self.run_local().returncode, 0)
        before = len(self.calls())

        result = self.run_local()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_lifecycle_calls(self.calls()[before:])
        self.assert_positive_log()

    def test_f_pin_change_restarts_without_sessions(self) -> None:
        self.assert_restarts_after_change(
            self.change_pin, expected_marker_fragment="legion-envoy-v2.0.0"
        )

    def test_f_unit_change_restarts_without_sessions(self) -> None:
        self.assert_restarts_after_change(self.change_unit)

    def test_f_wrapper_change_restarts_without_sessions(self) -> None:
        self.assert_restarts_after_change(self.change_wrapper)

    def test_f_broker_env_change_restarts_without_sessions(self) -> None:
        self.assert_restarts_after_change(self.change_broker_env)

    def test_g_pin_change_with_sessions_does_not_restart(self) -> None:
        self.assert_defers_restart_after_change(self.change_pin)

    def test_g_unit_change_with_sessions_does_not_restart(self) -> None:
        self.assert_defers_restart_after_change(self.change_unit)

    def test_g_wrapper_change_with_sessions_does_not_restart(self) -> None:
        self.assert_defers_restart_after_change(self.change_wrapper)

    def test_g_broker_env_change_with_sessions_does_not_restart(self) -> None:
        self.assert_defers_restart_after_change(self.change_broker_env)

    def test_g_pin_change_sessions_call_fails_does_not_restart(self) -> None:
        new_calls = self.assert_keeps_helper_when_sessions_unanswered(
            "(exit status 1)", STUB_SESSIONS_EXIT="1"
        )
        self.assertIn("helper sessions", new_calls)

    def test_g_pin_change_sessions_call_times_out_does_not_restart(self) -> None:
        new_calls = self.assert_keeps_helper_when_sessions_unanswered(
            "exit status 124: it timed out after 10 s", STUB_TIMEOUT_EXIT="124"
        )
        helper = Path(self.env["STUB_MISE_WHERE"]) / "bin" / "agent-secrets-helper"
        self.assertIn(f"timeout 10 {helper} sessions", new_calls)

    def test_g2_local_installer_drops_session_restart_override(self) -> None:
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        self.assertEqual(self.run_local().returncode, 0)
        before = len(self.calls())
        self.env["STUB_MISE_WHERE"] = str(self.make_mise_install("legion-envoy-v2.0.0"))
        self.env["STUB_SESSIONS"] = "registered-session\n"

        result = self.run_local(AGENT_SECRETS_HELPER_RESTART_WITH_SESSIONS="1")
        new_calls = self.calls()[before:]

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_lifecycle_calls(new_calls)
        self.assertIn("NOT restarted", result.stderr)
        self.assert_positive_log()

    def test_h_stopped_helper_is_started_again(self) -> None:
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        self.assertEqual(self.run_local().returncode, 0)
        (self.state / "active").unlink()
        before = len(self.calls())

        result = self.run_local()
        enables = self.systemctl_verb(self.calls()[before:], "enable")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(enables, [f"systemctl --user enable --now {UNIT_NAME}"])
        self.assert_positive_log()

    def make_helper_answer_only_after_startup(self, install: Path) -> None:
        """Model the real helper's start: `enable --now` returns before its socket listens.

        The unit is Type=simple and its ExecStart is a bash wrapper around `mise exec`, so
        systemctl returns as soon as the process forks. Measured on oryx with the pinned
        legion-envoy-v29.54.0 binary in a private XDG_RUNTIME_DIR: `agent-secrets-helper sessions`
        12 ms after `serve` starts exits 2 ("not answering at .../helper.sock"). Here the first
        `sessions` call after a start fails with exit 2 and the helper then listens; a helper that
        was already running answers at once.
        """
        write_stub(
            install / "bin",
            "agent-secrets-helper",
            'echo "helper $*" >>"$STUB_LOG"\n'
            'if [[ "$1" == sessions && ! -f "$STUB_STATE/listening" ]]; then\n'
            '    touch "$STUB_STATE/listening"\n'
            '    echo "agent-secrets-helper is not answering" >&2\n'
            "    exit 2\n"
            "fi\n"
            'printf "%s" "${STUB_SESSIONS:-}"',
        )

    def test_e2_unchanged_run_after_first_install_started_the_helper_is_quiet(self) -> None:
        # R1: a second run with the same pin, unit, wrapper and broker.env, while the helper
        # runs, makes no call that enables, starts or restarts it. The first install started the
        # helper itself, so the helper already runs the installed files.
        self.make_helper_answer_only_after_startup(self.mise_v1)
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        first = self.run_local()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn(f"systemctl --user enable --now {UNIT_NAME}", self.calls())
        before = len(self.calls())

        result = self.run_local()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_lifecycle_calls(self.calls()[before:])
        self.assert_positive_log()

    def test_h2_unchanged_run_after_starting_a_stopped_helper_on_a_new_pin_is_quiet(self) -> None:
        # A stopped helper is started on the new pin by `enable --now`, so it already runs the
        # installed version; the next unchanged run must not restart it.
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        self.assertEqual(self.run_local().returncode, 0)
        (self.state / "active").unlink()
        mise_v2 = self.make_mise_install("legion-envoy-v2.0.0")
        self.make_helper_answer_only_after_startup(mise_v2)
        self.env["STUB_MISE_WHERE"] = str(mise_v2)
        started = self.run_local()
        self.assertEqual(started.returncode, 0, started.stderr)
        before = len(self.calls())

        result = self.run_local()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_lifecycle_calls(self.calls()[before:])
        self.assert_positive_log()

    def test_h3_active_but_disabled_helper_on_a_new_pin_still_follows_the_restart_rules(
        self,
    ) -> None:
        # `enable --now` on an active but disabled helper enables it without restarting it, so
        # the helper still runs the old pin: the run did not start it, and the new pin is
        # recorded only through the sessions check and a restart.
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        self.assertEqual(self.run_local().returncode, 0)
        (self.state / "enabled").unlink()
        self.change_pin()
        before = len(self.calls())

        result = self.run_local()
        new_calls = self.calls()[before:]

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.systemctl_verb(new_calls, "enable"),
            [f"systemctl --user enable --now {UNIT_NAME}"],
        )
        self.assertIn("helper sessions", new_calls)
        self.assertEqual(
            self.systemctl_verb(new_calls, "try-restart"),
            [f"systemctl --user try-restart {UNIT_NAME}"],
        )
        for verb in ("start", "restart"):
            self.assertEqual(self.systemctl_verb(new_calls, verb), [], new_calls)
        self.assertIn("legion-envoy-v2.0.0", self.marker.read_text(encoding="utf-8"))
        self.assert_positive_log()

    def test_i_env_local_url_change_does_not_restart_the_helper(self) -> None:
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        self.assertEqual(self.run_local().returncode, 0)
        before = len(self.calls())
        self.write_env_local("AGENT_SECRETS_URL=https://other.example.invalid\n")

        result = self.run_local()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_lifecycle_calls(self.calls()[before:])
        self.assert_positive_log()

    def use_booting_helper(self, install: Path) -> None:
        write_stub(install / "bin", "agent-secrets-helper", BOOTING_HELPER_STUB)
        write_stub(self.stub_dir, "agent-secrets", LOGIN_STATUS_STUB)

    @staticmethod
    def helper_and_client_calls(lines: list[str]) -> list[str]:
        return [line for line in lines if line.startswith(("helper ", "agent-secrets "))]

    def assert_login_status_after_the_helper_answered(
        self, result: subprocess.CompletedProcess[str], new_calls: list[str], lifecycle: str
    ) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertNotIn("no live launcher credential", output)
        self.assertNotIn("machine login-status exited", output)
        after = new_calls[new_calls.index(f"systemctl --user {lifecycle} {UNIT_NAME}") + 1 :]
        # Three refusals, the first answer, and then the one login-status call.
        self.assertEqual(
            self.helper_and_client_calls(after),
            ["helper sessions"] * 4
            + ["helper answered sessions", "agent-secrets machine login-status"],
            new_calls,
        )

    def test_l_waits_for_the_restarted_helper_before_login_status(self) -> None:
        # INFRA-3887: `enable --now` and `try-restart` return before the new helper listens, so
        # login-status asked at once fails for that reason alone. The installer waits for the
        # helper it (re)started to answer `sessions`, then reads login-status once.
        self.use_booting_helper(self.mise_v1)
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")

        started = self.run_local()

        self.assert_login_status_after_the_helper_answered(
            started, self.calls(), "enable --now"
        )
        before = len(self.calls())
        self.change_pin()
        self.use_booting_helper(Path(self.env["STUB_MISE_WHERE"]))

        restarted = self.run_local()

        self.assert_login_status_after_the_helper_answered(
            restarted, self.calls()[before:], "try-restart"
        )
        self.assertIn("legion-envoy-v2.0.0", self.marker.read_text(encoding="utf-8"))
        # Neither run set AGENT_SECRETS_HELPER_SOCKET_WAIT, so each wait is the 10 s default.
        waits = [
            line for line in self.calls() if line.startswith("timeout ") and " bash -c " in line
        ]
        self.assertEqual([line.split(" ")[1] for line in waits], ["10", "10"], waits)

    def test_l2_a_failed_login_status_prints_its_own_error(self) -> None:
        url = "AGENT_SECRETS_URL=https://broker.example.invalid\n"
        self.use_booting_helper(self.mise_v1)
        self.write_env_local(url)
        self.assertEqual(self.run_local(STUB_REFUSALS="0").returncode, 0)
        # The reasons are legion-envoy-v30.21.1's (helper.NoCredentialReason).
        none = (
            "agent-secrets machine login-status: no machine login since the helper started, "
            "and none kept on this machine; run: agent-secrets machine login"
        )
        expired = (
            "agent-secrets machine login-status: the most recent machine login expired "
            "before anyone approved it; run: agent-secrets machine login"
        )
        dial = (
            "agent-secrets machine login-status: dial unix helper.sock: connect: "
            "no such file or directory"
        )
        identity = (
            "  the helper proves this machine's identity (AGENT_SECRETS_MACHINE_IDENTITY in "
            "~/.config/agent-secrets/env.local) and holds no launcher credential from it yet: "
            "journalctl --user -u agent-secrets-helper"
        )
        cases = {
            # `none` on a helper that proves no machine identity: no machine login has run, and
            # agent-secrets-login names the operator for the first one.
            "none": (url, {"STUB_LOGIN_STATE": "none", "STUB_LOGIN_REASON": none},
                     f"none\n{none}\n  run: agent-secrets-login <email>"),
            "expired": (url, {"STUB_LOGIN_STATE": "expired", "STUB_LOGIN_REASON": expired},
                        f"expired\n{expired}"),
            # A helper that proves the machine's identity (broker.Start's startIdentity) answers
            # `none` until its first trade: no person's login is missing.
            "none with a machine identity": (
                url + "AGENT_SECRETS_MACHINE_IDENTITY=metadata\n",
                {"STUB_LOGIN_STATE": "none", "STUB_LOGIN_REASON": none},
                f"none\n{none}\n{identity}",
            ),
            # No state on stdout: the client could not ask the helper.
            "no state": (url, {"STUB_UNREACHABLE": "1"}, dial),
        }
        for name, (env_local, stub, expected) in cases.items():
            with self.subTest(case=name):
                self.write_env_local(env_local)

                result = self.run_local(STUB_LOGIN_EXIT="1", **stub)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout, f"agent-secrets: machine login-status exited 1: {expected}\n"
                )
                self.assertNotIn("no live launcher credential", result.stdout + result.stderr)

    def test_l3_a_helper_that_never_answers_is_named(self) -> None:
        self.use_booting_helper(self.mise_v1)
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        wait = {"STUB_REFUSALS": "never", "AGENT_SECRETS_HELPER_SOCKET_WAIT": "1"}
        for lifecycle, how in (("enable --now", "start"), ("try-restart", "restart")):
            with self.subTest(how=how):
                if how == "restart":
                    # The helper the first run started came up after that run gave up on it.
                    (self.state / "booting").unlink()
                    self.change_pin()
                    self.use_booting_helper(Path(self.env["STUB_MISE_WHERE"]))
                before = len(self.calls())
                began = time.monotonic()

                result = self.run_local(**wait)

                elapsed = time.monotonic() - began
                new_calls = self.calls()[before:]
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"systemctl --user {lifecycle} {UNIT_NAME}", new_calls)
                self.assertIn(
                    "agent-secrets: the helper did not answer on its socket within 1 s after "
                    f"its {how} (systemctl --user status agent-secrets-helper); its login "
                    "state was not read\n",
                    result.stdout,
                )
                self.assertIn("helper sessions", new_calls)
                self.assertNotIn("agent-secrets machine login-status", new_calls)
                self.assertNotIn("no live launcher credential", result.stdout + result.stderr)
                # The 1 s wait, not the 10 s default, bounds the run.
                self.assertLess(elapsed, 8)

    def test_l4_no_restart_reads_login_status_at_once(self) -> None:
        self.use_booting_helper(self.mise_v1)
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        self.assertEqual(self.run_local(STUB_REFUSALS="0").returncode, 0)
        before = len(self.calls())

        result = self.run_local()

        new_calls = self.calls()[before:]
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_lifecycle_calls(new_calls)
        self.assertEqual(
            self.helper_and_client_calls(new_calls), ["agent-secrets machine login-status"]
        )

    def test_l5_none_uses_the_effective_machine_identity(self) -> None:
        self.use_booting_helper(self.mise_v1)
        url = "AGENT_SECRETS_URL=https://broker.example.invalid\n"
        self.write_env_local(url)
        self.assertEqual(self.run_local(STUB_REFUSALS="0").returncode, 0)
        cases = {
            "plain identity": ("AGENT_SECRETS_MACHINE_IDENTITY=metadata\n", True),
            "leading spaces": ("  AGENT_SECRETS_MACHINE_IDENTITY=metadata\n", True),
            "empty quotes": ('AGENT_SECRETS_MACHINE_IDENTITY=""\n', False),
            "last assignment empty": (
                "AGENT_SECRETS_MACHINE_IDENTITY=metadata\nAGENT_SECRETS_MACHINE_IDENTITY=\n",
                False,
            ),
            "inherited identity only": ("", False),
        }
        for name, (assignments, has_identity) in cases.items():
            with self.subTest(case=name):
                self.write_env_local(url + assignments)

                result = self.run_local(
                    AGENT_SECRETS_MACHINE_IDENTITY="inherited",
                    STUB_LOGIN_STATE="none",
                    STUB_LOGIN_EXIT="1",
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("machine login-status exited 1: none", result.stdout)
                if has_identity:
                    self.assertIn("the helper proves this machine's identity", result.stdout)
                    self.assertNotIn("run: agent-secrets-login <email>", result.stdout)
                else:
                    self.assertIn("run: agent-secrets-login <email>", result.stdout)
                    self.assertNotIn("the helper proves this machine's identity", result.stdout)

    def test_l6_invalid_socket_wait_uses_the_finite_default(self) -> None:
        self.use_booting_helper(self.mise_v1)
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        for wait_s in ("0", "0.0", "-1", "not-a-number"):
            with self.subTest(wait_s=wait_s):
                (self.state / "active").unlink(missing_ok=True)
                before = len(self.calls())
                began = time.monotonic()

                # The outer deadline also stops the whole poll loop on a broken installer.
                result = subprocess.run(
                    ["/usr/bin/timeout", "18", "bash", str(LOCAL_INSTALLER)],
                    capture_output=True,
                    text=True,
                    env={
                        **self.env,
                        "STUB_REFUSALS": "never",
                        "AGENT_SECRETS_HELPER_SOCKET_WAIT": wait_s,
                    },
                    check=False,
                    timeout=25,
                )

                elapsed = time.monotonic() - began
                new_calls = self.calls()[before:]
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(
                    result.stderr,
                    "agent-secrets: AGENT_SECRETS_HELPER_SOCKET_WAIT must be a positive "
                    "number of seconds; using 10 s\n",
                )
                self.assertIn("within 10 s after its start", result.stdout)
                self.assertIn("helper sessions", new_calls)
                self.assertNotIn("agent-secrets machine login-status", new_calls)
                self.assertGreaterEqual(elapsed, 9)
                self.assertLess(elapsed, 18)

    def test_l7_positive_fractional_socket_wait_stays_finite(self) -> None:
        self.use_booting_helper(self.mise_v1)
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        began = time.monotonic()

        result = self.run_local(
            STUB_REFUSALS="never", AGENT_SECRETS_HELPER_SOCKET_WAIT="0.2"
        )

        elapsed = time.monotonic() - began
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("within 0.2 s after its start", result.stdout)
        self.assertIn("helper sessions", self.calls())
        self.assertNotIn("agent-secrets machine login-status", self.calls())
        self.assertLess(elapsed, 8)

    def test_r1_root_installer_continues_when_helper_binary_is_absent(self) -> None:
        root_installer = self.prepare_root_installer()
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")
        self.env["STUB_MISE_WHERE"] = str(self.root / "mise" / "missing")

        result = self.run_root(root_installer)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING:", result.stdout)
        self.assertTrue((self.root / "knives-ran").is_file())

    def test_r2_root_installer_installs_with_env_local_url(self) -> None:
        root_installer = self.prepare_root_installer()
        self.write_env_local("AGENT_SECRETS_URL=https://broker.example.invalid\n")

        result = self.run_root(root_installer)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"systemctl --user enable --now {UNIT_NAME}", self.calls())
        self.assertTrue((self.root / "knives-ran").is_file())
        self.assert_positive_log()

    def test_r3_root_installer_skips_without_env_local_url(self) -> None:
        root_installer = self.prepare_root_installer()

        result = self.run_root(root_installer)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.lexists(self.unit))
        self.assertFalse(any(UNIT_NAME in line for line in self.calls()))
        self.assertTrue((self.root / "knives-ran").is_file())


if __name__ == "__main__":
    unittest.main()
