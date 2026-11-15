#!/usr/bin/env python3
"""Install test triggers as a pytest plugin, run a test file, or reap a runner."""

from __future__ import annotations

import argparse
import ctypes
import inspect
import json
import os
import runpy
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any


_PR_SET_CHILD_SUBREAPER = 36
_ORIGINAL_POPEN = subprocess.Popen
_ORIGINAL_DO_CLEANUPS = unittest.TestCase.doCleanups
_AGENT_TEST = "test_key_restricts_the_tunnel_and_login_agent_probe"
_OPTIONS_TEST = "test_options_match_the_tunnel_forwards"
_TRIGGER_NAMES = {
    "fail",
    "agent-term",
    "agent-int",
    "agent-hup",
    "agent-term-group",
    "run-sshd-test",
    "run-options-test",
    "sshd-gap",
    "cleanup-gap",
}


class Trigger:
    """One requested fault injection for a runner process."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.fired = False

    def claim(self) -> bool:
        if self.fired:
            return False
        self.fired = True
        return True


def _command_arguments(popenargs: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if popenargs:
        return popenargs[0]
    return kwargs.get("args")


def _replace_command(
    popenargs: tuple[Any, ...], kwargs: dict[str, Any], command: list[str]
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if popenargs:
        return (command, *popenargs[1:]), kwargs
    replacement = dict(kwargs)
    replacement["args"] = command
    return popenargs, replacement


def _command_name(arguments: Any) -> str | None:
    if isinstance(arguments, (str, bytes, os.PathLike)):
        return os.path.basename(os.fsdecode(arguments))
    try:
        first = arguments[0]
    except (IndexError, KeyError, TypeError):
        return None
    return os.path.basename(os.fspath(first))


def _has_argument(arguments: Any, value: str) -> bool:
    if isinstance(arguments, (str, bytes, os.PathLike)):
        return False
    try:
        return value in arguments
    except TypeError:
        return False


def _test_name() -> str | None:
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame is not None else None
        while frame is not None:
            name = frame.f_code.co_name
            if name.startswith("test_"):
                return name
            frame = frame.f_back
    finally:
        del frame
    return None


def _agent_pid_from_env(kwargs: dict[str, Any]) -> int:
    """The sshd test passes env=agent_env, which holds SSH_AGENT_PID, to its ssh-add call."""
    try:
        return int(kwargs["env"]["SSH_AGENT_PID"])
    except (KeyError, TypeError):
        raise RuntimeError("agent trigger requires env['SSH_AGENT_PID']") from None


def _process_state(pid: int) -> str | None:
    try:
        contents = Path("/proc") / str(pid) / "stat"
        _, fields = contents.read_bytes().rsplit(b")", 1)
    except (FileNotFoundError, ProcessLookupError):
        return None
    values = fields.split()
    if not values:
        raise RuntimeError(f"could not read process state for {pid}")
    return values[0].decode("ascii")


def _write_event(
    trigger: str,
    *,
    signum: int | None,
    target: str | None,
    test: str | None = None,
    **extra: Any,
) -> None:
    events = os.environ.get("ORYX_RIG_EVENTS")
    if not events:
        raise RuntimeError("ORYX_RIG_EVENTS is required for a trigger")
    event: dict[str, Any] = {
        "trigger": trigger,
        "pid": os.getpid(),
        "pgid": os.getpgrp(),
        "test": test if test is not None else _test_name(),
        "signal": signum,
        "target": target,
        **extra,
    }
    with Path(events).open("a", encoding="utf-8") as output:
        output.write(json.dumps(event) + "\n")


def _send_signal_and_wait(trigger: str, signum: int, target: str) -> None:
    if target == "group":
        os.killpg(os.getpgrp(), signum)
    else:
        os.kill(os.getpid(), signum)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        time.sleep(min(0.1, deadline - time.monotonic()))
    raise RuntimeError(
        f"trigger {trigger} sent {signal.Signals(signum).name} and the run went on"
    )


def _agent_signal(
    trigger: Trigger, signum: int, target: str, agent_pid: int
) -> None:
    agent_state = _process_state(agent_pid)
    agent_alive = agent_state is not None and agent_state != "Z"
    _write_event(
        trigger.name,
        signum=signum,
        target=target,
        agent_pid=agent_pid,
        agent_alive=agent_alive,
    )
    if not agent_alive:
        raise RuntimeError(f"agent trigger found ssh-agent {agent_pid} not alive")
    _send_signal_and_wait(trigger.name, signum, target)


def _timer_trigger(trigger: Trigger, process: subprocess.Popen[Any]) -> None:
    _write_event(
        trigger.name,
        signum=signal.SIGTERM,
        target="pid",
        child_pid=process.pid,
    )
    timer = threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGTERM))
    timer.daemon = True
    timer.start()


def install_trigger() -> None:
    """Install the requested Popen interception, if this process has one."""
    global _installed_trigger

    name = os.environ.get("ORYX_RIG_TRIGGER")
    if not name:
        return
    if name not in _TRIGGER_NAMES:
        raise RuntimeError(f"unknown ORYX_RIG_TRIGGER: {name}")
    if _installed_trigger is not None:
        if _installed_trigger.name != name:
            raise RuntimeError(
                f"already installed trigger {_installed_trigger.name}, cannot install {name}"
            )
        return

    trigger = Trigger(name)

    class TriggerPopen(_ORIGINAL_POPEN[Any]):
        def __init__(self, *popenargs: Any, **kwargs: Any) -> None:
            arguments = _command_arguments(popenargs, kwargs)
            command_name = _command_name(arguments)

            if name == "fail" and command_name == "ssh-add" and trigger.claim():
                agent_pid = _agent_pid_from_env(kwargs)
                _write_event(
                    name,
                    signum=None,
                    target=None,
                    agent_pid=agent_pid,
                )
                popenargs, kwargs = _replace_command(
                    popenargs, kwargs, ["/usr/bin/false"]
                )
            elif name in {
                "agent-term",
                "agent-int",
                "agent-hup",
                "agent-term-group",
            } and command_name == "ssh-add" and trigger.claim():
                agent_pid = _agent_pid_from_env(kwargs)
                signals = {
                    "agent-term": signal.SIGTERM,
                    "agent-int": signal.SIGINT,
                    "agent-hup": signal.SIGHUP,
                    "agent-term-group": signal.SIGTERM,
                }
                target = "group" if name == "agent-term-group" else "pid"
                _agent_signal(trigger, signals[name], target, agent_pid)
            elif name == "run-sshd-test" and command_name == "ssh-add" and trigger.claim():
                popenargs, kwargs = _replace_command(
                    popenargs, kwargs, ["/usr/bin/sleep", "60"]
                )
                self._oryx_rig_timer = True
            elif (
                name == "run-options-test"
                and command_name == "ssh"
                and _has_argument(arguments, "-G")
                and _test_name() == _OPTIONS_TEST
                and trigger.claim()
            ):
                popenargs, kwargs = _replace_command(
                    popenargs, kwargs, ["/usr/bin/sleep", "60"]
                )
                self._oryx_rig_timer = True
            else:
                self._oryx_rig_timer = False

            super().__init__(*popenargs, **kwargs)

            if name == "sshd-gap" and command_name == "sshd" and trigger.claim():
                _write_event(
                    name,
                    signum=signal.SIGTERM,
                    target="pid",
                    sshd_pid=self.pid,
                )
                _send_signal_and_wait(name, signal.SIGTERM, "pid")

        def communicate(self, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
            if getattr(self, "_oryx_rig_timer", False):
                _timer_trigger(trigger, self)
                self._oryx_rig_timer = False
            return super().communicate(*args, **kwargs)

    subprocess.Popen = TriggerPopen

    if name == "cleanup-gap":
        def do_cleanups(case: unittest.TestCase, *args: Any, **kwargs: Any) -> Any:
            if (
                case._testMethodName == _AGENT_TEST
                and trigger.claim()
            ):
                _write_event(
                    name,
                    signum=signal.SIGTERM,
                    target="pid",
                    test=case._testMethodName,
                )
                _send_signal_and_wait(name, signal.SIGTERM, "pid")
            return _ORIGINAL_DO_CLEANUPS(case, *args, **kwargs)

        unittest.TestCase.doCleanups = do_cleanups

    _installed_trigger = trigger


_installed_trigger: Trigger | None = None


def _set_child_subreaper() -> None:
    prctl = ctypes.CDLL(None, use_errno=True).prctl
    if prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _process_table() -> dict[int, tuple[str, int]]:
    processes: dict[int, tuple[str, int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            _, fields = (entry / "stat").read_bytes().rsplit(b")", 1)
        except (FileNotFoundError, ProcessLookupError):
            continue
        values = fields.split()
        if len(values) < 2:
            raise RuntimeError(f"could not parse {entry / 'stat'}")
        state = values[0].decode("ascii")
        parent_pid = int(values[1])
        processes[int(entry.name)] = (state, parent_pid)
    if os.getpid() not in processes:
        raise RuntimeError("full /proc scan did not find the reaper")
    return processes


def _descendants(processes: dict[int, tuple[str, int]]) -> set[int]:
    children: dict[int, list[int]] = {}
    for pid, (_, parent_pid) in processes.items():
        children.setdefault(parent_pid, []).append(pid)
    descendants: set[int] = set()
    pending = list(children.get(os.getpid(), []))
    while pending:
        pid = pending.pop()
        if pid in descendants:
            continue
        descendants.add(pid)
        pending.extend(children.get(pid, []))
    return descendants


def _describe_process(pid: int, state: str) -> dict[str, Any] | None:
    directory = Path("/proc") / str(pid)
    try:
        comm = (directory / "comm").read_bytes().decode(errors="replace").rstrip("\n")
        cmdline = (
            (directory / "cmdline")
            .read_bytes()
            .replace(b"\0", b" ")
            .decode(errors="replace")
            .rstrip()
        )
    except (FileNotFoundError, ProcessLookupError):
        return None
    return {"pid": pid, "state": state, "comm": comm, "cmdline": cmdline}


def _live_descendants(processes: dict[int, tuple[str, int]]) -> list[dict[str, Any]]:
    live: list[dict[str, Any]] = []
    for pid in sorted(_descendants(processes)):
        state, _ = processes[pid]
        if state == "Z":
            continue
        description = _describe_process(pid, state)
        if description is not None:
            live.append(description)
    return live


def _signal_pid(pid: int, signum: int) -> None:
    try:
        os.kill(pid, signum)
    except ProcessLookupError:
        pass


def _reap_direct_children(processes: dict[int, tuple[str, int]]) -> None:
    for pid, (_, parent_pid) in processes.items():
        if parent_pid != os.getpid():
            continue
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass


def _clean_descendants(leftovers: list[dict[str, Any]]) -> bool:
    for leftover in leftovers:
        _signal_pid(int(leftover["pid"]), signal.SIGTERM)

    term_deadline = time.monotonic() + 2
    while time.monotonic() < term_deadline:
        processes = _process_table()
        _reap_direct_children(processes)
        if not _live_descendants(processes):
            break
        time.sleep(0.05)

    kill_deadline = time.monotonic() + 10
    while True:
        processes = _process_table()
        descendants = _descendants(processes)
        if not descendants or time.monotonic() >= kill_deadline:
            return not descendants
        for pid in descendants:
            state, _ = processes[pid]
            if state != "Z":
                _signal_pid(pid, signal.SIGKILL)
        _reap_direct_children(processes)
        time.sleep(0.05)


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _reset_runner_signals() -> None:
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, signal.SIG_DFL)


def reap(events: Path, log: Path, timeout: float, runner_argv: list[str]) -> None:
    """Run one test command below this process and report its descendants."""
    if not runner_argv:
        raise RuntimeError("reap needs a runner command")

    _set_child_subreaper()
    stopped = False

    def stop_reaper(signum: int, frame: object) -> None:
        nonlocal stopped
        stopped = True

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, stop_reaper)

    saw_runner = False
    timed_out = False
    kill_sent = False
    deadline = time.monotonic() + timeout
    with Path(os.devnull).open("r", encoding="utf-8") as devnull, log.open("w", encoding="utf-8") as output:
        runner = _ORIGINAL_POPEN(
            runner_argv,
            stdin=devnull,
            stdout=output,
            stderr=output,
            process_group=0,
            preexec_fn=_reset_runner_signals,
        )
        while True:
            if not saw_runner:
                runner_process = _process_table().get(runner.pid)
                saw_runner = runner_process is not None and runner_process[1] == os.getpid()
            if (stopped or time.monotonic() >= deadline) and not kill_sent:
                timed_out = True
                try:
                    os.killpg(runner.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                kill_sent = True
            try:
                status = runner.wait(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                continue

    leftovers = _live_descendants(_process_table())
    clean_after = _clean_descendants(leftovers)
    result = {
        "runner_pid": runner.pid,
        "status": status,
        "saw_runner": saw_runner,
        "timed_out": timed_out,
        "leftovers": leftovers,
        "clean_after": clean_after,
        "events": _read_events(events),
    }
    print(json.dumps(result))


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("test_file")
    reaper = commands.add_parser("reap")
    reaper.add_argument("--events", type=Path, required=True)
    reaper.add_argument("--log", type=Path, required=True)
    reaper.add_argument("--timeout", type=float, required=True)
    reaper.add_argument("runner", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()

    if arguments.command == "run":
        install_trigger()
        sys.argv = [arguments.test_file]
        runpy.run_path(arguments.test_file, run_name="__main__")
        return

    runner_argv = arguments.runner
    if runner_argv[:1] == ["--"]:
        runner_argv = runner_argv[1:]
    reap(arguments.events, arguments.log, arguments.timeout, runner_argv)


if __name__ != "__main__":
    install_trigger()


if __name__ == "__main__":
    main()
