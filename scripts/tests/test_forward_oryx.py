#!/usr/bin/env python3
"""Tests for forward-oryx's private Cloudflare Access connection."""

from __future__ import annotations

import os
import pwd
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path


DOTFILES = Path(__file__).resolve().parents[2]
FORWARD = DOTFILES / "forward"
SSH = Path("/usr/bin/ssh")
TOKEN_ID = "fake-id.access"
TOKEN_SECRET = "fakesecret-forward-oryx"
NO_TOKEN_MESSAGE = (
    "forward-oryx: no Cloudflare Access token in ~/.config/forward/tunnel-oryx.env; "
    "run bash ~/.dotfiles/installers/forward.sh daemon"
)
BROWSER_REFUSED_MESSAGE = (
    "forward-oryx: cloudflared asked for a browser sign-in, so Cloudflare Access "
    "refused or lacks the service token; no browser was opened; "
    "run bash ~/.dotfiles/installers/forward.sh daemon"
)


def normalized_forward(value: str) -> str:
    return re.sub(r"\[([^\]]+)\]", r"\1", value)


def write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/bash\nset -uo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def process_is_alive(pid: int) -> bool:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        contents = stat_path.read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        # ProcessLookupError: the process exited between open and read.
        return False
    return not contents.rsplit(") ", 1)[1].startswith("Z")


def user_manager_is_running() -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-system-running"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    return result.stdout.strip() not in {"", "offline", "unknown"}


def values_from_ssh_g(config: Path, home: Path) -> dict[str, list[str]]:
    result = subprocess.run(
        [str(SSH), "-G", "-F", str(config), "oryx"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    values: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        key, value = line.split(maxsplit=1)
        values.setdefault(key, []).append(value)
    return values


class ForwardOryxSshConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.config = FORWARD / "forward-oryx.ssh-config"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def assert_no_tokens(self, *streams: tuple[str, str]) -> None:
        for stream, text in streams:
            for value in (TOKEN_ID, TOKEN_SECRET):
                self.assertFalse(value in text, f"{stream} holds the fake token")

    def test_settings_forwards_and_pinned_key(self) -> None:
        values = values_from_ssh_g(self.config, self.home)
        self.assertEqual(values["hostname"], ["oryx.thecybermonk.com"])
        self.assertEqual(values["user"], ["agent"])
        self.assertEqual(values["proxycommand"], ["cloudflared access ssh --hostname %h"])
        self.assertEqual(values["hostkeyalias"], ["oryx.tail12ec1c.ts.net"])
        self.assertEqual(values["globalknownhostsfile"], ["none"])
        self.assertEqual(values["stricthostkeychecking"], ["true"])
        self.assertEqual(values["identityfile"], ["~/.ssh/oryx-tunnel"])
        self.assertEqual(values["identitiesonly"], ["yes"])
        self.assertEqual(values["identityagent"], ["none"])
        self.assertEqual(values["forwardagent"], ["no"])
        self.assertEqual(values["controlmaster"], ["false"])
        self.assertNotIn("controlpath", values)
        self.assertIn("  ControlPath none\n", self.config.read_text(encoding="utf-8"))
        self.assertEqual(values["batchmode"], ["yes"])
        self.assertEqual(values["connecttimeout"], ["30"])
        self.assertEqual(values["exitonforwardfailure"], ["yes"])
        self.assertEqual(values["serveraliveinterval"], ["15"])
        self.assertEqual(values["serveralivecountmax"], ["4"])
        passwd_home = pwd.getpwuid(os.getuid()).pw_dir
        self.assertEqual(
            values["userknownhostsfile"],
            [f"{passwd_home}/.dotfiles/forward/forward-oryx.known_hosts"],
        )
        self.assertFalse(
            any(str(self.home) in value for value in values["userknownhostsfile"]),
            "ssh -G resolved UserKnownHostsFile under the scratch HOME",
        )
        self.assertCountEqual(
            [normalized_forward(value) for value in values["remoteforward"]],
            [
                "127.0.0.1:12800 127.0.0.1:12800",
                "127.0.0.1:12803 127.0.0.1:12803",
                "127.0.0.1:12804 127.0.0.1:12804",
                "127.0.0.1:12806 127.0.0.1:12806",
            ],
        )
        self.assertCountEqual(
            [normalized_forward(value) for value in values["localforward"]],
            [
                "127.0.0.1:12801 127.0.0.1:12801",
                "127.0.0.1:12802 127.0.0.1:12802",
                "127.0.0.1:12805 127.0.0.1:12805",
            ],
        )

        phone = (DOTFILES / "phone" / "termux-setup").read_text(encoding="utf-8")
        phone_match = re.search(r'^known_hosts_line="([^"]+)"$', phone, re.MULTILINE)
        self.assertIsNotNone(phone_match)
        phone_key = phone_match.group(1).split()[1:]
        known_host = (FORWARD / "forward-oryx.known_hosts").read_text(encoding="utf-8").split()
        self.assertEqual(known_host[0], "oryx.tail12ec1c.ts.net")
        self.assertEqual(known_host[1:], phone_key)

    def test_proxycommand_uses_token_environment_without_arguments(self) -> None:
        fake_bin = self.root / "bin"
        record = self.root / "cloudflared-record"
        write_executable(
            fake_bin / "cloudflared",
            '''{
    printf 'argv'
    for argument in "$@"; do printf ' [%s]' "$argument"; done
    printf '\\nTUNNEL_SERVICE_TOKEN_ID=%s\\n' "${TUNNEL_SERVICE_TOKEN_ID:+set}"
    printf 'TUNNEL_SERVICE_TOKEN_SECRET=%s\\n' "${TUNNEL_SERVICE_TOKEN_SECRET:+set}"
} > "$PROXY_RECORD"''',
        )
        values = values_from_ssh_g(self.config, self.home)
        command = values["proxycommand"][0].replace("%h", values["hostname"][0])
        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={
                "HOME": str(self.home),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "PROXY_RECORD": str(record),
                "TUNNEL_SERVICE_TOKEN_ID": TOKEN_ID,
                "TUNNEL_SERVICE_TOKEN_SECRET": TOKEN_SECRET,
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = record.read_text(encoding="utf-8")
        self.assertEqual(
            recorded.splitlines()[0],
            "argv [access] [ssh] [--hostname] [oryx.thecybermonk.com]",
        )
        self.assertIn("TUNNEL_SERVICE_TOKEN_ID=set", recorded)
        self.assertIn("TUNNEL_SERVICE_TOKEN_SECRET=set", recorded)
        self.assert_no_tokens(
            ("ProxyCommand record", recorded),
            ("ProxyCommand stdout", result.stdout),
            ("ProxyCommand stderr", result.stderr),
        )

    def test_token_argument_control_trips_the_recorded_argument_search(self) -> None:
        fake_bin = self.root / "bin"
        record = self.root / "cloudflared-record"
        write_executable(
            fake_bin / "cloudflared",
            '''{
    for argument in "$@"; do printf '%s\\n' "$argument"; done
} > "$PROXY_RECORD"''',
        )
        leaky_config = self.root / "forward-oryx.ssh-config"
        source = self.config.read_text(encoding="utf-8")
        original = "  ProxyCommand cloudflared access ssh --hostname %h\n"
        self.assertEqual(source.count(original), 1)
        leaky_config.write_text(
            source.replace(
                original,
                f"  ProxyCommand cloudflared access ssh --hostname %h --service-token-secret {TOKEN_SECRET}\n",
            ),
            encoding="utf-8",
        )
        values = values_from_ssh_g(leaky_config, self.home)
        command = values["proxycommand"][0].replace("%h", values["hostname"][0])
        subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={
                "HOME": str(self.home),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "PROXY_RECORD": str(record),
                "TUNNEL_SERVICE_TOKEN_ID": TOKEN_ID,
                "TUNNEL_SERVICE_TOKEN_SECRET": TOKEN_SECRET,
            },
        )
        recorded = record.read_text(encoding="utf-8")
        self.assertTrue(TOKEN_SECRET in recorded)
        with self.assertRaises(AssertionError):
            self.assert_no_tokens(("argument record", recorded))


class ForwardOryxWrapper(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runtime = self.root / "runtime"
        self.home = self.root / "home"
        self.runtime.mkdir()
        self.home.mkdir()
        self.wrapper = self.root / "forward-oryx"
        self.daemon = self.root / "forward-daemon"
        self.ssh = self.root / "ssh"
        self.daemon_record = self.root / "daemon-record"
        self.ssh_record = self.root / "ssh-record"
        self.daemon_pid = self.root / "daemon-pid"
        self.ssh_pid = self.root / "ssh-pid"
        shutil.copyfile(FORWARD / "forward-oryx.ssh-config", self.root / "forward-oryx.ssh-config")
        shutil.copyfile(FORWARD / "forward-oryx.known_hosts", self.root / "forward-oryx.known_hosts")
        shutil.copyfile(FORWARD / "config-daemon-oryx.toml", self.root / "config-daemon-oryx.toml")
        self.write_stubs()

    def tearDown(self) -> None:
        self.doCleanups()
        self.temp_dir.cleanup()

    def assert_no_tokens(self, *streams: tuple[str, str]) -> None:
        for stream, text in streams:
            for value in (TOKEN_ID, TOKEN_SECRET):
                self.assertFalse(value in text, f"{stream} holds the fake token")

    def assert_only_line_changes(
        self, before: str, after: str, expected: list[tuple[str, str]]
    ) -> None:
        before_lines = before.splitlines(keepends=True)
        after_lines = after.splitlines(keepends=True)
        self.assertEqual(len(before_lines), len(after_lines))
        changed = [
            (old, new)
            for old, new in zip(before_lines, after_lines)
            if old != new
        ]
        self.assertEqual(changed, expected)

    def write_stubs(self) -> None:
        # Each stub writes its pid file LAST, once its traps and its record are in place, so a pid
        # file means that child is fully up. A stub told to exit waits (15 s at most) for its peer's
        # pid file first: the wrapper stops the peer as soon as one child exits, and a peer stopped
        # before it started wrote no pid file, so under load "daemon-pid was not created" failed
        # test_either_child_exit_stops_the_other on sami-devbox at cb78d6a9 and at this change's
        # head, while each test passed alone. "Either child's exit stops the other" needs the other
        # running when the first one exits; that is what this waits for.
        peer_wait = '''for ((i = 0; i < 300; i++)); do [[ -s "$%s" ]] && break; sleep 0.05; done'''
        write_executable(
            self.daemon,
            '''if [[ "${DAEMON_IGNORE_TERM:-}" == 1 ]]; then
    trap '' TERM INT
else
    trap 'exit 0' TERM INT
fi
{
    printf 'TUNNEL_SERVICE_TOKEN_ID=%%s\\n' "${TUNNEL_SERVICE_TOKEN_ID:+set}"
    printf 'TUNNEL_SERVICE_TOKEN_SECRET=%%s\\n' "${TUNNEL_SERVICE_TOKEN_SECRET:+set}"
} > "$DAEMON_RECORD"
printf '%%s\\n' "$$" > "$DAEMON_PID"
if [[ -v DAEMON_EXIT_STATUS ]]; then
    %s
    exit "$DAEMON_EXIT_STATUS"
fi
while :; do sleep 1; done''' % (peer_wait % "SSH_PID"),
        )
        write_executable(
            self.ssh,
            '''if [[ "${SSH_IGNORE_TERM:-}" == 1 ]]; then
    trap '' TERM INT
else
    trap 'exit 0' TERM INT
fi
{
    printf 'TUNNEL_SERVICE_TOKEN_ID=%%s\\n' "${TUNNEL_SERVICE_TOKEN_ID:+set}"
    printf 'TUNNEL_SERVICE_TOKEN_SECRET=%%s\\n' "${TUNNEL_SERVICE_TOKEN_SECRET:+set}"
    printf 'DISPLAY=%%s\\n' "${DISPLAY+x}"
    printf 'WAYLAND_DISPLAY=%%s\\n' "${WAYLAND_DISPLAY+x}"
    printf 'BROWSER=%%s\\n' "${BROWSER+x}"
    printf 'HOME=%%s\\n' "$HOME"
    printf 'PATH=%%s\\n' "$PATH"
} > "$SSH_RECORD"
if [[ "${SSH_RUN_XDG_OPEN:-}" == 1 ]]; then
    xdg-open https://login.example.invalid || true
fi
printf '%%s\\n' "$$" > "$SSH_PID"
if [[ -v SSH_EXIT_STATUS ]]; then
    %s
    exit "$SSH_EXIT_STATUS"
fi
while :; do sleep 1; done''' % (peer_wait % "DAEMON_PID"),
        )

    def write_wrapper(self, *, ssh_path: Path | None) -> None:
        source = (FORWARD / "forward-oryx").read_text(encoding="utf-8")
        copied = source
        if ssh_path is not None:
            original = "ssh=/usr/bin/ssh\n"
            replacement = f"ssh={ssh_path}\n"
            self.assertEqual(source.count(original), 1)
            copied = source.replace(original, replacement)
            self.assert_only_line_changes(source, copied, [(original, replacement)])
        self.wrapper.write_text(copied, encoding="utf-8")
        self.wrapper.chmod(0o755)

    def wrapper_env(self, **extra: str) -> dict[str, str]:
        env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin",
            "RUNTIME_DIRECTORY": str(self.runtime),
            "DAEMON_PID": str(self.daemon_pid),
            "DAEMON_RECORD": str(self.daemon_record),
            "SSH_PID": str(self.ssh_pid),
            "SSH_RECORD": str(self.ssh_record),
        }
        env.update(extra)
        return env

    def run_wrapper(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.wrapper)],
            cwd=self.root,
            env=self.wrapper_env(**extra),
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )

    def wait_for_path(self, path: Path, timeout: float = 5) -> None:
        deadline = time.monotonic() + timeout
        while not path.exists():
            self.assertLess(time.monotonic(), deadline, f"{path} was not created")
            time.sleep(0.02)

    def pid_from(self, path: Path) -> int:
        self.wait_for_path(path)
        return int(path.read_text(encoding="utf-8").strip())

    def wait_for_not_alive(self, pid: int, timeout: float = 5) -> None:
        deadline = time.monotonic() + timeout
        while process_is_alive(pid):
            self.assertLess(time.monotonic(), deadline, f"pid {pid} survived")
            time.sleep(0.02)

    def test_missing_or_empty_token_starts_nothing(self) -> None:
        self.write_wrapper(ssh_path=self.ssh)
        cases = (
            ("missing ID", {"TUNNEL_SERVICE_TOKEN_SECRET": TOKEN_SECRET}),
            ("missing secret", {"TUNNEL_SERVICE_TOKEN_ID": TOKEN_ID}),
            (
                "empty ID",
                {
                    "TUNNEL_SERVICE_TOKEN_ID": "",
                    "TUNNEL_SERVICE_TOKEN_SECRET": TOKEN_SECRET,
                },
            ),
            (
                "empty secret",
                {
                    "TUNNEL_SERVICE_TOKEN_ID": TOKEN_ID,
                    "TUNNEL_SERVICE_TOKEN_SECRET": "",
                },
            ),
        )
        for name, tokens in cases:
            with self.subTest(name=name):
                result = self.run_wrapper(**tokens)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, NO_TOKEN_MESSAGE + "\n")
                self.assertFalse(self.daemon_record.exists(), "daemon started without a token")
                self.assertFalse(self.ssh_record.exists(), "ssh started without a token")

    def test_unwritable_runtime_stops_before_children(self) -> None:
        self.write_wrapper(ssh_path=self.ssh)
        original_mode = stat.S_IMODE(self.runtime.stat().st_mode)
        self.addCleanup(self.runtime.chmod, original_mode)
        self.runtime.chmod(0o500)

        result = self.run_wrapper(
            TUNNEL_SERVICE_TOKEN_ID=TOKEN_ID,
            TUNNEL_SERVICE_TOKEN_SECRET=TOKEN_SECRET,
            DAEMON_EXIT_STATUS="0",
        )

        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.daemon_record.exists(), "daemon started after setup failed")
        self.assertFalse(self.ssh_record.exists(), "ssh started after setup failed")
        self.assertIn(
            "forward-oryx: could not create runtime directories:",
            result.stderr,
        )

    def test_children_receive_only_their_required_environment(self) -> None:
        self.write_wrapper(ssh_path=self.ssh)
        result = self.run_wrapper(
            TUNNEL_SERVICE_TOKEN_ID=TOKEN_ID,
            TUNNEL_SERVICE_TOKEN_SECRET=TOKEN_SECRET,
            DISPLAY="wayland-9",
            WAYLAND_DISPLAY="wayland-9",
            BROWSER="browser",
            SSH_RUN_XDG_OPEN="1",
            SSH_EXIT_STATUS="7",
        )
        self.assertEqual(result.returncode, 1)
        daemon = self.daemon_record.read_text(encoding="utf-8")
        ssh = self.ssh_record.read_text(encoding="utf-8")
        self.assertIn("TUNNEL_SERVICE_TOKEN_ID=\n", daemon)
        self.assertIn("TUNNEL_SERVICE_TOKEN_SECRET=\n", daemon)
        self.assertIn("TUNNEL_SERVICE_TOKEN_ID=set\n", ssh)
        self.assertIn("TUNNEL_SERVICE_TOKEN_SECRET=set\n", ssh)
        self.assertIn("DISPLAY=\n", ssh)
        self.assertIn("WAYLAND_DISPLAY=\n", ssh)
        self.assertIn("BROWSER=\n", ssh)
        self.assertIn(f"HOME={self.runtime / 'home'}\n", ssh)
        self.assertIn(f"PATH={self.runtime / 'bin'}:/usr/local/bin:/usr/bin:/bin\n", ssh)
        self.assertTrue((self.runtime / "browser-refused").exists())
        self.assertIn(
            str(self.runtime / "browser-refused"),
            (self.runtime / "bin" / "xdg-open").read_text(encoding="utf-8"),
        )
        opener = subprocess.run(
            [str(self.runtime / "bin" / "xdg-open")],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(opener.returncode, 1)
        self.assertEqual(opener.stdout, "")
        self.assertEqual(opener.stderr, "")
        self.assertIn(BROWSER_REFUSED_MESSAGE, result.stderr)
        self.assert_no_tokens(
            ("wrapper stdout", result.stdout),
            ("wrapper stderr", result.stderr),
            ("daemon record", daemon),
            ("ssh record", ssh),
        )

    def test_browser_refusal_message_requires_marker(self) -> None:
        self.write_wrapper(ssh_path=self.ssh)
        result = self.run_wrapper(
            TUNNEL_SERVICE_TOKEN_ID=TOKEN_ID,
            TUNNEL_SERVICE_TOKEN_SECRET=TOKEN_SECRET,
            SSH_EXIT_STATUS="7",
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.runtime / "browser-refused").exists())
        self.assertFalse(BROWSER_REFUSED_MESSAGE in result.stderr)
        self.assert_no_tokens(
            ("wrapper stdout", result.stdout),
            ("wrapper stderr", result.stderr),
        )

    def test_refused_token_through_the_real_proxycommand_path(self) -> None:
        self.write_wrapper(ssh_path=None)
        cached_login = self.home / ".cloudflared" / "access-token"
        cached_login.parent.mkdir()
        cached_login.write_text("cached login\n", encoding="utf-8")
        cloudflared_record = self.root / "cloudflared-record"
        write_executable(
            self.runtime / "bin" / "cloudflared",
            '''cache=no
shopt -s nullglob
tokens=("$HOME"/.cloudflared/*-token)
(( ${#tokens[@]} == 0 )) || cache=yes
{
    printf 'cache=%s\\n' "$cache"
    printf 'argv'
    for argument in "$@"; do printf ' [%s]' "$argument"; done
    printf '\\n'
} > "$CLOUDFLARED_RECORD"
xdg-open https://login.example.invalid >/dev/null 2>&1 &
opener=$!
wait "$opener" || true
printf 'A browser window should have opened at the following URL\\n' >&2
exit 1''',
        )
        started = time.monotonic()
        result = self.run_wrapper(
            TUNNEL_SERVICE_TOKEN_ID=TOKEN_ID,
            TUNNEL_SERVICE_TOKEN_SECRET=TOKEN_SECRET,
            CLOUDFLARED_RECORD=str(cloudflared_record),
        )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 20)
        self.assertEqual(result.returncode, 1)
        record = cloudflared_record.read_text(encoding="utf-8")
        self.assertEqual(record.splitlines()[0], "cache=no")
        self.assertEqual(
            record.splitlines()[1],
            "argv [access] [ssh] [--hostname] [oryx.thecybermonk.com]",
        )
        self.assertIn("forward-oryx: ssh exited with status", result.stderr)
        self.assertIn(BROWSER_REFUSED_MESSAGE, result.stderr)
        self.assertTrue((self.runtime / "browser-refused").exists())
        self.assertTrue((self.runtime / "bin" / "cloudflared").exists())
        self.assert_no_tokens(
            ("wrapper stdout", result.stdout),
            ("wrapper stderr", result.stderr),
            ("cloudflared record", record),
        )

    def test_either_child_exit_stops_the_other(self) -> None:
        for child in ("daemon", "ssh"):
            for status in ("0", "17"):
                with self.subTest(child=child, status=status):
                    self.write_wrapper(ssh_path=self.ssh)
                    extra = {
                        "TUNNEL_SERVICE_TOKEN_ID": TOKEN_ID,
                        "TUNNEL_SERVICE_TOKEN_SECRET": TOKEN_SECRET,
                        f"{child.upper()}_EXIT_STATUS": status,
                    }
                    result = self.run_wrapper(**extra)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(
                        f"forward-oryx: {child} exited with status {status}; ",
                        result.stderr,
                    )
                    daemon_pid = self.pid_from(self.daemon_pid)
                    ssh_pid = self.pid_from(self.ssh_pid)
                    self.wait_for_not_alive(daemon_pid)
                    self.wait_for_not_alive(ssh_pid)
                    self.assert_no_tokens(
                        ("wrapper stdout", result.stdout),
                        ("wrapper stderr", result.stderr),
                    )
                    self.daemon_record.unlink(missing_ok=True)
                    self.ssh_record.unlink(missing_ok=True)
                    self.daemon_pid.unlink(missing_ok=True)
                    self.ssh_pid.unlink(missing_ok=True)

    def test_term_ignoring_survivor_is_killed_within_seven_seconds(self) -> None:
        self.write_wrapper(ssh_path=self.ssh)
        started = time.monotonic()
        result = self.run_wrapper(
            TUNNEL_SERVICE_TOKEN_ID=TOKEN_ID,
            TUNNEL_SERVICE_TOKEN_SECRET=TOKEN_SECRET,
            DAEMON_EXIT_STATUS="0",
            SSH_IGNORE_TERM="1",
        )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 7)
        self.assertEqual(result.returncode, 1)
        self.assertIn("forward-oryx: daemon exited with status 0; ", result.stderr)
        self.wait_for_not_alive(self.pid_from(self.ssh_pid))
        self.assert_no_tokens(
            ("wrapper stdout", result.stdout),
            ("wrapper stderr", result.stderr),
        )

    def test_term_to_wrapper_ends_both_children(self) -> None:
        self.write_wrapper(ssh_path=self.ssh)
        process = subprocess.Popen(
            ["bash", str(self.wrapper)],
            cwd=self.root,
            env=self.wrapper_env(
                TUNNEL_SERVICE_TOKEN_ID=TOKEN_ID,
                TUNNEL_SERVICE_TOKEN_SECRET=TOKEN_SECRET,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        daemon_pid = self.pid_from(self.daemon_pid)
        ssh_pid = self.pid_from(self.ssh_pid)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 143)
        self.wait_for_not_alive(daemon_pid)
        self.wait_for_not_alive(ssh_pid)
        self.assert_no_tokens(
            ("wrapper stdout", stdout),
            ("wrapper stderr", stderr),
        )


@unittest.skipUnless(user_manager_is_running(), "systemd user manager is not running")
class ForwardOryxLiveUnit(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.name = f"forward-oryx-test-{uuid.uuid4().hex}.service"
        self.unit = self.root / self.name
        self.token_file = self.root / "tunnel-oryx.env"
        self.wrapper = self.root / "forward-oryx"
        self.daemon_pid_file = self.root / "daemon-pid"
        self.ssh_pid_file = self.root / "ssh-pid"
        self.ssh_stub = self.root / "ssh"
        self.link_path = Path.home() / ".config" / "systemd" / "user" / self.name

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def systemctl(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["systemctl", "--user", *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def assert_command_ok(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def replace_one_line(self, source: str, old: str, new: str) -> str:
        self.assertEqual(source.count(old), 1)
        return source.replace(old, new)

    def assert_only_line_changes(
        self, before: str, after: str, expected: list[tuple[str, str]]
    ) -> None:
        before_lines = before.splitlines(keepends=True)
        after_lines = after.splitlines(keepends=True)
        self.assertEqual(len(before_lines), len(after_lines))
        changed = [
            (old, new)
            for old, new in zip(before_lines, after_lines)
            if old != new
        ]
        self.assertEqual(changed, expected)

    def cleanup_unit(self) -> None:
        self.systemctl("stop", self.name)
        self.systemctl("disable", self.name)
        if self.link_path.is_symlink():
            self.link_path.unlink()
        self.systemctl("daemon-reload")
        self.systemctl("reset-failed", self.name)
        self.temp_dir.cleanup()

    def prepare(self) -> None:
        original_unit = (FORWARD / "forward-oryx.service").read_text(encoding="utf-8")
        environment_old = "EnvironmentFile=%h/.config/forward/tunnel-oryx.env\n"
        environment_new = f"EnvironmentFile={self.token_file}\n"
        exec_old = "ExecStart=%h/.dotfiles/forward/forward-oryx\n"
        exec_new = f"ExecStart={self.wrapper}\n"
        changed_unit = self.replace_one_line(original_unit, environment_old, environment_new)
        changed_unit = self.replace_one_line(changed_unit, exec_old, exec_new)
        self.assert_only_line_changes(
            original_unit,
            changed_unit,
            [(environment_old, environment_new), (exec_old, exec_new)],
        )
        self.unit.write_text(changed_unit, encoding="utf-8")

        original_wrapper = (FORWARD / "forward-oryx").read_text(encoding="utf-8")
        ssh_old = "ssh=/usr/bin/ssh\n"
        ssh_new = f"ssh={self.ssh_stub}\n"
        changed_wrapper = self.replace_one_line(original_wrapper, ssh_old, ssh_new)
        self.assert_only_line_changes(original_wrapper, changed_wrapper, [(ssh_old, ssh_new)])
        self.wrapper.write_text(changed_wrapper, encoding="utf-8")
        self.wrapper.chmod(0o755)
        for name in ("forward-oryx.ssh-config", "forward-oryx.known_hosts", "config-daemon-oryx.toml"):
            shutil.copyfile(FORWARD / name, self.root / name)
        self.token_file.write_text(
            f"TUNNEL_SERVICE_TOKEN_ID={TOKEN_ID}\nTUNNEL_SERVICE_TOKEN_SECRET={TOKEN_SECRET}\n",
            encoding="utf-8",
        )
        self.token_file.chmod(0o600)
        write_executable(
            self.root / "forward-daemon",
            f'''printf '%s\\n' "$$" > "{self.daemon_pid_file}"
trap 'exit 0' TERM INT
while :; do sleep 300 & wait "$!"; done''',
        )
        write_executable(
            self.ssh_stub,
            f'''printf '%s\\n' "$$" > "{self.ssh_pid_file}"
trap 'exit 0' TERM INT
while :; do sleep 300 & wait "$!"; done''',
        )

    def pid_from(self, path: Path) -> int:
        return int(path.read_text(encoding="utf-8").strip())

    def pids(self) -> tuple[int, int] | None:
        try:
            return self.pid_from(self.daemon_pid_file), self.pid_from(self.ssh_pid_file)
        except (FileNotFoundError, ValueError):
            return None

    def wait_for(self, condition: object, timeout: float = 30) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            self.assertLess(time.monotonic(), deadline, "condition did not become true")
            time.sleep(0.05)

    def show(self, *properties: str) -> dict[str, str]:
        result = self.systemctl("show", self.name, *[item for property in properties for item in ("-p", property)])
        self.assert_command_ok(result)
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def nrestarts(self) -> int:
        return int(self.show("NRestarts")["NRestarts"])

    def start(self) -> None:
        self.prepare()
        self.addCleanup(self.cleanup_unit)
        self.assert_command_ok(self.systemctl("link", str(self.unit)))
        self.assert_command_ok(self.systemctl("start", self.name))
        self.wait_for(lambda: self.pids() is not None)

    def recover_after(self, trigger: object, label: str) -> tuple[int, int]:
        before = self.pids()
        self.assertIsNotNone(before)
        old_daemon, old_ssh = before
        restarts_before = self.nrestarts()
        started = time.monotonic()
        trigger(old_daemon, old_ssh)

        def recovered() -> bool:
            pids = self.pids()
            return (
                pids is not None
                and pids[0] != old_daemon
                and pids[1] != old_ssh
                and process_is_alive(pids[0])
                and process_is_alive(pids[1])
                and not process_is_alive(old_daemon)
                and not process_is_alive(old_ssh)
                and self.nrestarts() > restarts_before
            )

        self.wait_for(recovered)
        elapsed = time.monotonic() - started
        print(f"{label} recovery: {elapsed:.3f}s")
        return self.pids()  # type: ignore[return-value]

    def test_process_group_and_recovery(self) -> None:
        self.start()
        initial = self.pids()
        self.assertIsNotNone(initial)
        daemon_pid, ssh_pid = initial
        self.wait_for(lambda: process_is_alive(daemon_pid) and process_is_alive(ssh_pid))
        properties = self.show("MainPID", "ControlGroup", "FragmentPath")
        self.assertNotEqual(properties["MainPID"], "0")
        self.assertEqual(Path(properties["FragmentPath"]), self.link_path)
        self.assertTrue(self.link_path.is_symlink())
        self.assertEqual(self.link_path.resolve(), self.unit)
        cgroup_procs = Path("/sys/fs/cgroup") / properties["ControlGroup"].lstrip("/") / "cgroup.procs"
        cgroup_pids = {int(value) for value in cgroup_procs.read_text(encoding="utf-8").splitlines()}
        self.assertIn(daemon_pid, cgroup_pids)
        self.assertIn(ssh_pid, cgroup_pids)
        status = self.systemctl("status", self.name)
        self.assert_command_ok(status)
        self.assertIn(str(daemon_pid), status.stdout)
        self.assertIn(str(ssh_pid), status.stdout)

        self.recover_after(lambda _daemon, ssh: os.kill(ssh, signal.SIGTERM), "ssh kill")
        self.recover_after(lambda daemon, _ssh: os.kill(daemon, signal.SIGTERM), "daemon exit 0")

        before_restart = self.pids()
        self.assertIsNotNone(before_restart)
        old_daemon, old_ssh = before_restart
        started = time.monotonic()
        self.assert_command_ok(self.systemctl("restart", self.name))
        self.wait_for(
            lambda: (
                self.pids() is not None
                and self.pids()[0] != old_daemon
                and self.pids()[1] != old_ssh
                and process_is_alive(self.pids()[0])
                and process_is_alive(self.pids()[1])
                and not process_is_alive(old_daemon)
                and not process_is_alive(old_ssh)
            )
        )
        print(f"systemctl restart recovery: {time.monotonic() - started:.3f}s")


if __name__ == "__main__":
    unittest.main()
