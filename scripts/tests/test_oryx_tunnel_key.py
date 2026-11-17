#!/usr/bin/env python3
"""The forwarding-only key that the parent installs on oryx."""

from __future__ import annotations

import ctypes
import os
import pwd
import random
import re
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path


DOTFILES = Path(__file__).resolve().parents[2]
SSH = Path("/usr/bin/ssh")
SSH_ADD = Path("/usr/bin/ssh-add")
SSH_AGENT = Path("/usr/bin/ssh-agent")
SSH_KEYGEN = Path("/usr/bin/ssh-keygen")
SSHD = Path("/usr/sbin/sshd")
FORWARD_ORYX_SSH_CONFIG = DOTFILES / "forward" / "forward-oryx.ssh-config"
KEY_OPTIONS = DOTFILES / "oryx" / "oryx-tunnel.key-options"

_PROC = Path("/proc")
_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37
_PRCTL = ctypes.CDLL(None, use_errno=True).prctl


class _StoppedBySignal(BaseException):
    """A signal deferred until the test finalizer has stopped its children."""


def process_table() -> dict[int, tuple[str, int, int]]:
    """Return each process's state, parent pid, and start tick from /proc."""
    processes: dict[int, tuple[str, int, int]] = {}
    for directory in _PROC.iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            _, fields = (directory / "stat").read_bytes().rsplit(b")", 1)
        except (FileNotFoundError, ProcessLookupError):
            continue
        values = fields.split()
        state = values[0].decode("ascii")
        parent_pid = int(values[1])
        start_tick = int(values[19])
        processes[int(directory.name)] = (state, parent_pid, start_tick)
    if os.getpid() not in processes:
        raise AssertionError("full /proc scan did not find this process")
    return processes


def current_boot_tick() -> int:
    uptime = float((_PROC / "uptime").read_text(encoding="utf-8").split()[0])
    return int(uptime * os.sysconf("SC_CLK_TCK")) - 1


def get_child_subreaper() -> int:
    value = ctypes.c_int()
    if _PRCTL(_PR_GET_CHILD_SUBREAPER, ctypes.byref(value), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return value.value


def set_child_subreaper(value: int) -> None:
    if _PRCTL(_PR_SET_CHILD_SUBREAPER, value, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def process_cmdline(pid: int) -> str:
    try:
        return (_PROC / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").rstrip()
    except (FileNotFoundError, ProcessLookupError):
        return "<vanished>"


def read_options_line() -> str:
    lines = [
        line
        for line in KEY_OPTIONS.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    if len(lines) != 1:
        raise AssertionError(f"expected one key-options line, got {lines!r}")
    return lines[0]


def option_tokens(line: str) -> list[str]:
    return re.split(r',(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)', line)


def option_values(tokens: list[str], name: str) -> set[str]:
    prefix = f"{name}="
    return {token.removeprefix(prefix).strip('"') for token in tokens if token.startswith(prefix)}


def free_port() -> int:
    """A port free now and below the kernel's ephemeral range (32768-60999 by default), so no
    other process's bind(0) or outgoing connect is handed it before ssh binds it. A port from
    bind(0) is ephemeral: on sami-devbox, where many sessions run tests, another process took one
    between this pick and ssh's bind, and read_forward read b"" from that stranger before ssh had
    even authenticated, so the next assertion failed on an ssh that forwarded nothing."""
    low = int(Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()[0])
    for port in random.sample(range(20000, min(low, 32768)), 200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port below the ephemeral range")


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        os.kill(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.kill(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def ssh_g(config: Path, host: str, home: Path) -> dict[str, list[str]]:
    result = subprocess.run(
        [str(SSH), "-F", str(config), "-G", host],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, "HOME": str(home)},
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    values: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        key, value = line.split(maxsplit=1)
        values.setdefault(key, []).append(value)
    return values


def forward_parts(value: str) -> tuple[str, str]:
    listen, target = value.split(maxsplit=1)
    return re.sub(r"\[([^\]]+)\]", r"\1", listen), re.sub(r"\[([^\]]+)\]", r"\1", target)


def forward_argument(listen: str, target: str) -> str:
    target_host, target_port = target.rsplit(":", 1)
    return f"{listen}:{target_host}:{target_port}"


class TcpServer:
    def __init__(self) -> None:
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(3)
        self.port = int(self.listener.getsockname()[1])
        self.connected = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def serve(self) -> None:
        try:
            connection, _ = self.listener.accept()
        except OSError:
            return
        with connection:
            self.connected.set()
            connection.sendall(b"ok")

    def close(self) -> None:
        self.listener.close()
        self.thread.join(timeout=5)


class UnixServer:
    def __init__(self, path: Path) -> None:
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path))
        self.listener.listen()
        self.listener.settimeout(1)
        self.connected = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def serve(self) -> None:
        try:
            connection, _ = self.listener.accept()
        except OSError:
            return
        with connection:
            self.connected.set()

    def close(self) -> None:
        self.listener.close()
        self.thread.join(timeout=5)


class OryxTunnelKey(unittest.TestCase):
    def setUp(self) -> None:
        self._finish_registered = False
        self._final_phase_started = False
        self._sweep_done = False
        self._subreaper_restored = False
        self._temp_dir_cleaned = False
        self._handlers_restored = False
        self._signal_redelivered = False
        self._recorded_signal: int | None = None
        self._previous_subreaper: int | None = None
        self._handlers: dict[int, object] = {}
        self._initial_children: set[tuple[int, int]] = set()
        self._boot_tick = 0
        self._sweep_ready = False
        self.addCleanup(self._finish)
        self._finish_registered = True
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self._stderr_files: dict[int, Path] = {}
        self.home = self.root / "home"
        self.home.mkdir()
        self._previous_subreaper = get_child_subreaper()
        set_child_subreaper(1)
        processes = process_table()
        self._initial_children = {
            (pid, start_time)
            for pid, (_, parent_pid, start_time) in processes.items()
            if parent_pid == os.getpid()
        }
        self._boot_tick = current_boot_tick()
        self._sweep_ready = True
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous = signal.getsignal(signum)
            if previous is not None and previous is not signal.SIG_IGN:
                self._handlers[signum] = previous
                signal.signal(signum, self._stop_by_signal)

    def _stop_by_signal(self, signum: int, frame: object) -> None:
        if self._recorded_signal is None:
            self._recorded_signal = signum
            if not self._final_phase_started:
                raise _StoppedBySignal(signum)

    def _owned_children(self, processes: dict[int, tuple[str, int, int]]) -> dict[int, tuple[str, int, int]]:
        return {
            pid: process
            for pid, process in processes.items()
            if (
                process[1] == os.getpid()
                and (pid, process[2]) not in self._initial_children
                and process[2] >= self._boot_tick
            )
        }

    def _sweep(self) -> None:
        if not self._sweep_ready:
            return
        deadline = time.monotonic() + 10
        terminated: dict[tuple[int, int], float] = {}
        killed: set[tuple[int, int]] = set()
        empty_since: float | None = None
        while True:
            processes = process_table()
            owned = self._owned_children(processes)
            now = time.monotonic()
            for pid, (state, _, start_time) in owned.items():
                child = (pid, start_time)
                if state != "Z":
                    if child not in terminated:
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        terminated[child] = now
                    elif now - terminated[child] >= 3 and child not in killed:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        killed.add(child)
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass
            if not owned:
                if empty_since is not None and now - empty_since >= 0.05:
                    return
                empty_since = now
            else:
                empty_since = None
            if now >= deadline:
                remaining = "; ".join(
                    f"pid={pid} state={state} cmdline={process_cmdline(pid)!r}"
                    for pid, (state, _, _) in owned.items()
                )
                raise AssertionError(f"timed out reaping test children: {remaining}")
            time.sleep(min(0.05, deadline - now))

    def _finish(self) -> None:
        if not getattr(self, "_finish_registered", False):
            return
        self._final_phase_started = True
        steps = (
            ("_sweep_done", self._sweep),
            ("_subreaper_restored", self._restore_subreaper),
            ("_temp_dir_cleaned", self._clean_temp_dir),
            ("_handlers_restored", self._restore_handlers),
        )
        first_error: BaseException | None = None
        for flag, step in steps:
            if getattr(self, flag):
                continue
            setattr(self, flag, True)
            try:
                step()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if self._recorded_signal is not None and not self._signal_redelivered:
            self._signal_redelivered = True
            signal.raise_signal(self._recorded_signal)
        if first_error is not None:
            raise first_error

    def _restore_subreaper(self) -> None:
        if self._previous_subreaper is not None:
            set_child_subreaper(self._previous_subreaper)

    def _clean_temp_dir(self) -> None:
        temp_dir = getattr(self, "temp_dir", None)
        if temp_dir is not None:
            temp_dir.cleanup()

    def _restore_handlers(self) -> None:
        for signum, previous in self._handlers.items():
            signal.signal(signum, previous)

    def run(self, result: unittest.TestResult | None = None) -> unittest.TestResult | None:
        try:
            return super().run(result)
        finally:
            self.doCleanups()
            self._finish()

    def test_options_match_the_tunnel_forwards(self) -> None:
        values = ssh_g(FORWARD_ORYX_SSH_CONFIG, "oryx", self.home)
        remote_listens = {forward_parts(value)[0] for value in values.get("remoteforward", [])}
        local_targets = {forward_parts(value)[1] for value in values.get("localforward", [])}
        line = read_options_line()
        tokens = option_tokens(line)
        expected = {
            "restrict",
            "port-forwarding",
            'command="echo oryx-tunnel: forwarding-only key"',
            *{f'permitlisten="{address}"' for address in remote_listens},
            *{f'permitopen="{address}"' for address in local_targets},
        }
        self.assertEqual(set(tokens), expected)
        self.assertEqual(len(tokens), len(expected))
        self.assertEqual(option_values(tokens, "permitlisten"), remote_listens)
        self.assertEqual(option_values(tokens, "permitopen"), local_targets)
        self.assertEqual(sum(token.startswith("command=") for token in tokens), 1)

    def test_key_restricts_the_tunnel_and_login_agent_probe(self) -> None:
        self.assertTrue(SSHD.is_file(), f"{SSHD} is required for this test")
        key_options = read_options_line()
        tunnel_values = ssh_g(FORWARD_ORYX_SSH_CONFIG, "oryx", self.home)
        remote_forwards = [forward_parts(value) for value in tunnel_values.get("remoteforward", [])]
        local_forwards = [forward_parts(value) for value in tunnel_values.get("localforward", [])]
        endpoints = {listen for listen, _ in remote_forwards} | {target for _, target in local_forwards}
        mapped: dict[str, str] = {}
        tcp_server = TcpServer()
        tcp_server.start()
        self.addCleanup(tcp_server.close)
        for endpoint in endpoints:
            if endpoint == "127.0.0.1:12801":
                mapped[endpoint] = f"127.0.0.1:{tcp_server.port}"
            else:
                mapped[endpoint] = f"127.0.0.1:{free_port()}"

        def remap(endpoint: str) -> str:
            return mapped.get(endpoint, endpoint)

        endpoint_pattern = "|".join(re.escape(endpoint) for endpoint in sorted(endpoints))
        remapped_options = re.sub(
            rf'"({endpoint_pattern})"',
            lambda match: f'"{remap(match.group(1))}"',
            key_options,
        )
        self.assertNotEqual(remapped_options, key_options)
        for endpoint in endpoints:
            self.assertNotIn(f'"{endpoint}"', remapped_options)

        host_key = self.root / "host_key"
        tunnel_key = self.root / "tunnel_key"
        login_key = self.root / "login_key"
        for path, comment in ((host_key, "host"), (tunnel_key, "tunnel"), (login_key, "login")):
            result = subprocess.run(
                [str(SSH_KEYGEN), "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        tunnel_public = tunnel_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
        login_public = login_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
        authorized_keys = self.root / "authorized_keys"
        authorized_keys.write_text(f"{remapped_options} {tunnel_public}\n{login_public}\n", encoding="utf-8")

        sshd_port = free_port()
        pid_file = self.root / "sshd.pid"
        sshd_log = self.root / "sshd.log"
        sshd_config = self.root / "sshd_config"
        sshd_config.write_text(
            "\n".join(
                (
                    f"Port {sshd_port}",
                    "ListenAddress 127.0.0.1",
                    f"HostKey {host_key}",
                    f"PidFile {pid_file}",
                    f"AuthorizedKeysFile {authorized_keys}",
                    "PasswordAuthentication no",
                    "KbdInteractiveAuthentication no",
                    "UsePAM no",
                    "StrictModes no",
                    "AllowStreamLocalForwarding no",
                    "LogLevel VERBOSE",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        sshd = self.spawn([str(SSHD), "-D", "-f", str(sshd_config), "-E", str(sshd_log)])
        self._sshd_pid = sshd.pid
        self.addCleanup(stop_process, sshd)
        self.wait_for_sshd(sshd, pid_file, sshd_port, sshd_log)

        user = pwd.getpwuid(os.getuid()).pw_name

        client_config = self.root / "client_config"
        client_config.write_text(
            "\n".join(
                (
                    "Host restricted",
                    "  HostName 127.0.0.1",
                    f"  Port {sshd_port}",
                    f"  User {user}",
                    f"  IdentityFile {tunnel_key}",
                    "  IdentitiesOnly yes",
                    "  IdentityAgent none",
                    "  StrictHostKeyChecking no",
                    "  UserKnownHostsFile /dev/null",
                    "  BatchMode yes",
                    "  ExitOnForwardFailure yes",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        ssh_env = {**os.environ, "HOME": str(self.home)}

        forced_true = self.ssh(client_config, ssh_env, "restricted", command="true")
        self.assertEqual(forced_true.returncode, 0, forced_true.stderr)
        self.assertIn("oryx-tunnel: forwarding-only key", forced_true.stdout)
        forced_id = self.ssh(client_config, ssh_env, "restricted", command="id")
        self.assertEqual(forced_id.returncode, 0, forced_id.stderr)
        self.assertNotIn("uid=", forced_id.stdout)

        for listen, target in remote_forwards:
            process = self.spawn(
                [str(SSH), "-F", str(client_config), "-N", "-R", forward_argument(remap(listen), remap(target)), "restricted"],
                ssh_env,
            )
            self.addCleanup(stop_process, process)
            self.wait_for_forward(process, int(remap(listen).rsplit(":", 1)[1]))

        unlisted_remote = free_port()
        blocked_remote = self.ssh(
            client_config,
            ssh_env,
            "restricted",
            "-N",
            "-R",
            forward_argument(f"127.0.0.1:{unlisted_remote}", f"127.0.0.1:{free_port()}"),
        )
        self.assertEqual(blocked_remote.returncode, 255, blocked_remote.stderr)

        local_port = free_port()
        allowed_local = self.spawn(
            [
                str(SSH), "-F", str(client_config), "-N", "-L",
                forward_argument(f"127.0.0.1:{local_port}", mapped["127.0.0.1:12801"]), "restricted",
            ],
            ssh_env,
        )
        self.addCleanup(stop_process, allowed_local)
        self.assertEqual(self.read_forward(allowed_local, local_port), b"ok")
        self.assertTrue(tcp_server.connected.wait(2))

        blocked_target = TcpServer()
        blocked_target.start()
        self.addCleanup(blocked_target.close)
        blocked_local_port = free_port()
        blocked_local = self.spawn(
            [
                str(SSH), "-vv", "-F", str(client_config), "-N", "-L",
                forward_argument(f"127.0.0.1:{blocked_local_port}", f"127.0.0.1:{blocked_target.port}"), "restricted",
            ],
            ssh_env,
        )
        self.addCleanup(stop_process, blocked_local)
        self.assertEqual(self.read_forward(blocked_local, blocked_local_port), b"")
        self.assertFalse(blocked_target.connected.wait(0.2))
        stop_process(blocked_local)
        self.assertIn("administratively prohibited", self.stderr_of(blocked_local))

        direct_stream = self.ssh(client_config, ssh_env, "restricted", "-W", "127.0.0.1:22")
        self.assertEqual(direct_stream.returncode, 255, direct_stream.stderr)

        remote_socket = self.root / "remote.sock"
        local_socket = self.root / "local.sock"
        blocked_socket_remote = self.ssh(
            client_config,
            ssh_env,
            "restricted",
            "-N",
            "-R",
            f"{remote_socket}:{local_socket}",
        )
        self.assertEqual(blocked_socket_remote.returncode, 255, blocked_socket_remote.stderr)

        unix_target = self.root / "target.sock"
        unix_server = UnixServer(unix_target)
        unix_server.start()
        self.addCleanup(unix_server.close)
        blocked_socket_local_port = free_port()
        blocked_socket_local = self.spawn(
            [
                str(SSH), "-vv", "-F", str(client_config), "-N", "-L",
                f"127.0.0.1:{blocked_socket_local_port}:{unix_target}", "restricted",
            ],
            ssh_env,
        )
        self.addCleanup(stop_process, blocked_socket_local)
        self.assertEqual(self.read_forward(blocked_socket_local, blocked_socket_local_port), b"")
        stop_process(blocked_socket_local)
        self.assertIn("open failed", self.stderr_of(blocked_socket_local))
        self.assertFalse(unix_server.connected.wait(0.2))

        agent = subprocess.run([str(SSH_AGENT), "-s"], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(agent.returncode, 0, agent.stderr)
        agent_env = dict(ssh_env)
        for line in agent.stdout.splitlines():
            if line.startswith("SSH_AUTH_SOCK=") or line.startswith("SSH_AGENT_PID="):
                name, value = line.split(";", 1)[0].split("=", 1)
                agent_env[name] = value
        self.assertIn("SSH_AUTH_SOCK", agent_env)
        self.assertIn("SSH_AGENT_PID", agent_env)
        agent_pid = int(agent_env["SSH_AGENT_PID"])
        self.addCleanup(self.stop_agent, agent_pid)
        added = subprocess.run([str(SSH_ADD), str(login_key)], capture_output=True, text=True, timeout=30, check=False, env=agent_env)
        self.assertEqual(added.returncode, 0, added.stderr)
        login_key.unlink()
        login_key.with_suffix(".pub").unlink()

        login_config = self.root / "login_config"
        login_config.write_text(
            "\n".join(
                (
                    "Host login",
                    "  HostName 127.0.0.1",
                    f"  Port {sshd_port}",
                    f"  User {user}",
                    f"  IdentityAgent {agent_env['SSH_AUTH_SOCK']}",
                    "  IdentitiesOnly no",
                    "  StrictHostKeyChecking no",
                    "  UserKnownHostsFile /dev/null",
                    "  BatchMode yes",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        login = self.ssh(login_config, agent_env, "login", command="id")
        self.assertEqual(login.returncode, 0, login.stderr)
        self.assertIn("uid=", login.stdout)
        self.assertNotIn("oryx-tunnel", login.stdout)

        no_agent_config = self.root / "login-no-agent-config"
        no_agent_config.write_text(login_config.read_text(encoding="utf-8").replace("IdentitiesOnly no", "IdentitiesOnly yes"), encoding="utf-8")
        no_agent = self.ssh(no_agent_config, agent_env, "login", command="id")
        self.assertEqual(no_agent.returncode, 255, no_agent.stderr)

    def ssh(
        self,
        config: Path,
        env: dict[str, str],
        host: str,
        *options: str,
        command: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        arguments = [str(SSH), "-F", str(config), *options, host]
        if command is not None:
            arguments.append(command)
        return subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )

    def wait_for_sshd(self, process: subprocess.Popen[str], pid_file: Path, port: int, log: Path) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stderr = self.stderr_of(process)
                self.fail(f"sshd exited before startup: {stderr}\n{log.read_text(encoding='utf-8') if log.exists() else ''}")
            if pid_file.exists():
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        return
                except OSError:
                    pass
            time.sleep(0.05)
        self.fail(f"sshd did not start: {log.read_text(encoding='utf-8') if log.exists() else ''}")

    def spawn(self, argv: list[str], env: dict[str, str] | None = None) -> subprocess.Popen[str]:
        """ARGV in the background, its stdout on /dev/null and its stderr in a file of its own (stderr_of).
        A pipe nobody reads until the process ends stalls a process that fills it: with its stderr pipe
        set to 4 KiB (F_SETPIPE_SZ), the `ssh -vv` below blocked in anon_pipe_write before it read the
        server's refusal, and the forward never answered (124 s, 2026-11-17). A new pipe gets that
        little room once this uid's pipes pass fs.pipe-user-pages-soft, and on sami-devbox, where this
        uid runs about 60,000 tasks, six of these tests run at once failed at that step 5 times in 6,
        their ssh logs ending at the same line. A file never fills."""
        path = self.root / f"process-{len(self._stderr_files) + 1}.stderr"
        with open(path, "w", encoding="utf-8") as stderr:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
                                       text=True, env=env)
        self._stderr_files[process.pid] = path
        return process

    def stderr_of(self, process: subprocess.Popen[str]) -> str:
        return self._stderr_files[process.pid].read_text(encoding="utf-8", errors="replace")

    def stuck(self, process: subprocess.Popen[str]) -> str:
        """Where PROCESS and sshd are when a wait runs out, so a failure says why rather than only
        that the port never answered: each one's state and wait channel, and PROCESS's stderr tail."""
        lines = []
        for name, pid in (("client", process.pid), ("sshd", getattr(self, "_sshd_pid", None))):
            if pid is None:
                continue
            try:
                state = next(line for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                             if line.startswith("State:"))
                wchan = Path(f"/proc/{pid}/wchan").read_text()
            except (OSError, StopIteration):
                state, wchan = "gone", "-"
            lines.append(f"{name} {pid}: {state}, waiting in {wchan or '-'}")
        return "; ".join(lines) + f"\nclient stderr tail:\n{self.stderr_of(process)[-1500:]}"

    def wait_for_forward(self, process: subprocess.Popen[str], port: int) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(f"permitted remote forward exited: {self.stderr_of(process)}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        self.fail(f"remote forward did not listen on {port}; {self.stuck(process)}")

    def read_forward(self, process: subprocess.Popen[str], port: int) -> bytes:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(f"local forward exited: {self.stderr_of(process)}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1) as connection:
                    connection.settimeout(5)
                    return connection.recv(2)
            except OSError:
                time.sleep(0.05)
        self.fail(f"local forward did not listen on {port}; {self.stuck(process)}")

    @staticmethod
    def stop_agent(pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


if __name__ == "__main__":
    unittest.main()
