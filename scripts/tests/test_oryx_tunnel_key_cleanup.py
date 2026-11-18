#!/usr/bin/env python3
"""Prove every tunnel-key test outcome cleans up its process tree.

The matrix covers a normal run, an assertion failure after the ssh-agent starts,
and every signal or cleanup gap exercised by ``oryx_tunnel_key_rig``.  The rig's
reaper is a child subreaper so that descendants which outlive their direct
parent are reparented to it, recorded as leftovers, and reaped before the next
case.  A class-level helper with a non-UTF-8 process name stays alive for every
host-wide scan.  The final in-process test proves that the test class restores
the caller's signal handlers and child-subreaper state while leaving an unrelated
child alone.
"""

from __future__ import annotations

import ctypes
import importlib.machinery
import importlib.util
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import warnings
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock


_PR_GET_CHILD_SUBREAPER = 37
_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
_AGENT_TEST = "test_key_restricts_the_tunnel_and_login_agent_probe"
_OPTIONS_TEST = "test_options_match_the_tunnel_forwards"

_EXPECTED_STATUS = {
    "python3": {
        "normal": 0,
        "fail": 1,
        "agent-term": -15,
        "agent-int": -2,
        "agent-hup": -1,
        "agent-term-group": -15,
        "run-sshd-test": -15,
        "run-options-test": -15,
        "sshd-gap": -15,
        "cleanup-gap": -15,
    },
    "pytest": {
        "normal": 0,
        "fail": 1,
        "agent-term": -15,
        "agent-int": 2,
        "agent-hup": -1,
        "agent-term-group": -15,
        "run-sshd-test": -15,
        "run-options-test": -15,
        "sshd-gap": -15,
        "cleanup-gap": -15,
    },
}

_TRIGGER_DETAILS = {
    "fail": (None, None),
    "agent-term": (signal.SIGTERM, "pid"),
    "agent-int": (signal.SIGINT, "pid"),
    "agent-hup": (signal.SIGHUP, "pid"),
    "agent-term-group": (signal.SIGTERM, "group"),
    "run-sshd-test": (signal.SIGTERM, "pid"),
    "run-options-test": (signal.SIGTERM, "pid"),
    "sshd-gap": (signal.SIGTERM, "pid"),
    "cleanup-gap": (signal.SIGTERM, "pid"),
}


@dataclass
class ReaperRun:
    """The data needed to assess one isolated reaper invocation."""

    case: str
    runner: str
    returncode: int | None
    stdout: str
    stderr: str
    log: str
    result: dict[str, Any] | None
    error: str | None


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _child_subreaper() -> int:
    value = ctypes.c_int()
    prctl = ctypes.CDLL(None, use_errno=True).prctl
    if prctl(_PR_GET_CHILD_SUBREAPER, ctypes.byref(value), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return value.value


def _own_children() -> dict[tuple[int, int], tuple[str, str, str]]:
    """Return this process's direct children keyed by pid and start tick."""

    children: dict[tuple[int, int], tuple[str, str, str]] = {}
    saw_self = False
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            _, fields = (entry / "stat").read_bytes().rsplit(b")", 1)
            values = fields.split()
            pid = int(entry.name)
            state = values[0].decode("ascii")
            parent_pid = int(values[1])
            start_tick = int(values[19])
            saw_self = saw_self or pid == os.getpid()
            if parent_pid != os.getpid():
                continue
            comm = (entry / "comm").read_bytes().decode(errors="replace").rstrip("\n")
            cmdline = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode(errors="replace")
                .rstrip()
            )
        except (FileNotFoundError, ProcessLookupError):
            continue
        children[(pid, start_tick)] = (state, comm, cmdline)
    if not saw_self:
        raise AssertionError("full /proc scan did not find this cleanup test")
    return children


def _stop_and_reap(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


class OryxTunnelKeyCleanup(unittest.TestCase):
    @staticmethod
    def _stop_non_utf8_name_helper(process: subprocess.Popen[bytes]) -> None:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        for output in (process.stdout, process.stderr):
            if output is not None:
                output.close()

    @classmethod
    def setUpClass(cls) -> None:
        try:
            pytest_python = subprocess.run(
                [
                    "uvx",
                    "--from",
                    "pytest==9.2.0",
                    "python",
                    "-c",
                    "import sys; print(sys.executable)",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise AssertionError(_as_text(error.stderr)) from error
        if pytest_python.returncode:
            raise AssertionError(pytest_python.stderr)

        cls.pytest_python = pytest_python.stdout.strip()
        if not cls.pytest_python:
            raise AssertionError("uvx did not print a pytest interpreter")
        cls.python3 = shutil.which("python3")
        if cls.python3 is None:
            raise AssertionError("python3 was not found")
        cls.test_file = Path(__file__).with_name("test_oryx_tunnel_key.py")
        cls.rig = Path(__file__).with_name("oryx_tunnel_key_rig.py")
        cls.tests_dir = cls.test_file.parent
        helper = subprocess.Popen(
            [
                cls.python3,
                "-c",
                (
                    "import ctypes, sys\n"
                    "PR_SET_NAME = 15\n"
                    "prctl = ctypes.CDLL(None, use_errno=True).prctl\n"
                    "if prctl(PR_SET_NAME, b'\\xffbadname', 0, 0, 0):\n"
                    "    raise OSError(ctypes.get_errno(), 'prctl(PR_SET_NAME)')\n"
                    "sys.stdout.buffer.write(b'ready\\n')\n"
                    "sys.stdout.buffer.flush()\n"
                    "sys.stdin.buffer.read()\n"
                ),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        cls.addClassCleanup(cls._stop_non_utf8_name_helper, helper)
        if helper.stdout is None:
            raise AssertionError("non-UTF-8 name helper has no stdout")
        ready = helper.stdout.readline()
        if ready != b"ready\n":
            raise AssertionError(f"non-UTF-8 name helper did not become ready: {ready!r}")
        try:
            comm = (Path("/proc") / str(helper.pid) / "comm").read_bytes()
        except FileNotFoundError as error:
            raise AssertionError("non-UTF-8 name helper vanished") from error
        if not comm.startswith(b"\xffbadname"):
            raise AssertionError(f"non-UTF-8 name helper has unexpected comm: {comm!r}")

    @classmethod
    def _run_reaper(cls, case: str, runner: str) -> ReaperRun:
        with tempfile.TemporaryDirectory(prefix="oryx-tunnel-key-cleanup-") as directory:
            scratch = Path(directory)
            events = scratch / "events.jsonl"
            log = scratch / "runner.log"
            environment = {
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "ORYX_RIG_EVENTS": str(events),
            }
            environment.pop("ORYX_RIG_TRIGGER", None)
            if case != "normal":
                environment["ORYX_RIG_TRIGGER"] = case

            if runner == "python3":
                runner_argv = [cls.python3, str(cls.test_file)]
                if case != "normal":
                    runner_argv = [cls.python3, str(cls.rig), "run", str(cls.test_file)]
            else:
                runner_argv = [
                    cls.pytest_python,
                    "-m",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                    "-q",
                    str(cls.test_file),
                ]
                if case != "normal":
                    environment["PYTHONPATH"] = str(cls.tests_dir)
                    runner_argv = [
                        cls.pytest_python,
                        "-m",
                        "pytest",
                        "-p",
                        "oryx_tunnel_key_rig",
                        "-p",
                        "no:cacheprovider",
                        "-q",
                        str(cls.test_file),
                    ]

            try:
                completed = subprocess.run(
                    [
                        cls.python3,
                        str(cls.rig),
                        "reap",
                        "--events",
                        str(events),
                        "--log",
                        str(log),
                        "--timeout",
                        "120",
                        "--",
                        *runner_argv,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                    env=environment,
                )
            except subprocess.TimeoutExpired as error:
                return ReaperRun(
                    case,
                    runner,
                    None,
                    _as_text(error.stdout),
                    _as_text(error.stderr),
                    log.read_text(encoding="utf-8") if log.exists() else "",
                    None,
                    "reaper timed out after 180 seconds",
                )
            except Exception as error:
                return ReaperRun(
                    case,
                    runner,
                    None,
                    "",
                    "",
                    log.read_text(encoding="utf-8") if log.exists() else "",
                    None,
                    f"reaper could not run: {error}",
                )

            result: dict[str, Any] | None = None
            error: str | None = None
            try:
                decoded = json.loads(completed.stdout)
                if not isinstance(decoded, dict):
                    raise ValueError(f"expected an object, got {type(decoded).__name__}")
                result = decoded
            except (json.JSONDecodeError, ValueError) as parse_error:
                error = f"reaper JSON is absent or invalid: {parse_error}"
            return ReaperRun(
                case,
                runner,
                completed.returncode,
                completed.stdout,
                completed.stderr,
                log.read_text(encoding="utf-8") if log.exists() else "",
                result,
                error,
            )

    @staticmethod
    def _leftover_text(leftovers: Any) -> str:
        if not isinstance(leftovers, list):
            return repr(leftovers)
        return "; ".join(
            "pid={pid} comm={comm!r} cmdline={cmdline!r}".format(
                pid=leftover.get("pid", "<missing>") if isinstance(leftover, dict) else "<missing>",
                comm=leftover.get("comm", "<missing>") if isinstance(leftover, dict) else "<missing>",
                cmdline=leftover.get("cmdline", "<missing>") if isinstance(leftover, dict) else repr(leftover),
            )
            for leftover in leftovers
        )

    def _matrix_failures(self, run: ReaperRun) -> list[str]:
        failures: list[str] = []
        expected_status = _EXPECTED_STATUS[run.runner][run.case]
        if run.returncode != 0:
            failures.append(
                f"reaper exit status {run.returncode}; stderr={run.stderr!r}; stdout={run.stdout!r}"
            )
        if run.error is not None or run.result is None:
            failures.append(run.error or "reaper did not return JSON")
            return failures

        result = run.result
        actual_status = result.get("status")
        if actual_status != expected_status:
            log_tail = "\n".join(run.log.splitlines()[-30:])
            failures.append(
                f"runner status expected {expected_status}, got {actual_status}; runner log tail:\n{log_tail}"
            )
        if result.get("saw_runner") is not True:
            failures.append(f"reaper did not see its runner: {result.get('saw_runner')!r}")
        if result.get("timed_out") is not False:
            failures.append(f"reaper timed out: {result.get('timed_out')!r}")
        leftovers = result.get("leftovers")
        if leftovers != []:
            failures.append(f"live leftovers: {self._leftover_text(leftovers)}")
        if result.get("clean_after") is not True:
            failures.append(f"reaper did not clean descendants: {result.get('clean_after')!r}")

        events = result.get("events")
        if run.case == "normal":
            if events != []:
                failures.append(f"normal run recorded events: {events!r}")
        elif not isinstance(events, list) or len(events) != 1:
            failures.append(f"expected one event for {run.case}, got {events!r}")
        else:
            event = events[0]
            if not isinstance(event, dict):
                failures.append(f"event is not an object: {event!r}")
            else:
                expected_signal, expected_target = _TRIGGER_DETAILS[run.case]
                if event.get("trigger") != run.case:
                    failures.append(
                        f"event trigger expected {run.case!r}, got {event.get('trigger')!r}"
                    )
                runner_pid = result.get("runner_pid")
                if event.get("pid") != runner_pid:
                    failures.append(
                        f"event pid expected runner pid {runner_pid!r}, got {event.get('pid')!r}"
                    )
                expected_test = _OPTIONS_TEST if run.case == "run-options-test" else _AGENT_TEST
                if event.get("test") != expected_test:
                    failures.append(
                        f"event test expected {expected_test!r}, got {event.get('test')!r}"
                    )
                if event.get("signal") != expected_signal:
                    failures.append(
                        f"event signal expected {expected_signal!r}, got {event.get('signal')!r}"
                    )
                if event.get("target") != expected_target:
                    failures.append(
                        f"event target expected {expected_target!r}, got {event.get('target')!r}"
                    )
                if run.case.startswith("agent-") and event.get("agent_alive") is not True:
                    failures.append(
                        f"agent trigger did not find a live agent: {event.get('agent_alive')!r}"
                    )

        if run.case == "fail":
            if "added.returncode" not in run.log:
                failures.append("failure log does not contain added.returncode")
            expected_failure = "FAILED (failures=1)" if run.runner == "python3" else "1 failed"
            if expected_failure not in run.log:
                failures.append(f"failure log does not contain {expected_failure!r}")
        if run.case == "normal":
            expected_output = ("Ran 2 tests", "OK") if run.runner == "python3" else ("2 passed",)
            for text in expected_output:
                if text not in run.log:
                    failures.append(f"normal log does not contain {text!r}")
        return failures

    def test_each_case_leaves_nothing_and_keeps_mains_exit_status(self) -> None:
        cases = tuple(_EXPECTED_STATUS["python3"])
        jobs = [(case, runner) for case in cases for runner in ("python3", "pytest")]
        # One run at a time: with three at once on oryx, the file under test missed its own
        # 10 s forward deadline ("local forward did not listen") in 6 of 240 runs on 2026-11-14.
        runs = [self._run_reaper(case, runner) for case, runner in jobs]

        for run in runs:
            status = run.result.get("status") if run.result is not None else "<no JSON>"
            expected = _EXPECTED_STATUS[run.runner][run.case]
            print(
                f"cleanup matrix {run.case} {run.runner}: measured {status}, expected {expected}",
                file=sys.stderr,
            )
            with self.subTest(case=run.case, runner=run.runner):
                failures = self._matrix_failures(run)
                self.assertFalse(failures, "\n".join(failures))

    def _post_run_failures(
        self,
        initial_children: dict[tuple[int, int], tuple[str, str, str]],
        previous_handlers: dict[int, object],
        previous_subreaper: int,
    ) -> list[str]:
        failures: list[str] = []
        for signum, previous in previous_handlers.items():
            current = signal.getsignal(signum)
            if current is not previous:
                failures.append(
                    f"{signal.Signals(signum).name} handler changed from {previous!r} to {current!r}"
                )
        current_subreaper = _child_subreaper()
        if current_subreaper != previous_subreaper:
            failures.append(
                f"child subreaper changed from {previous_subreaper} to {current_subreaper}"
            )

        # A new zombie is a child the test failed to reap, so it counts as a leftover too.
        new_children = {
            child: details
            for child, details in _own_children().items()
            if child not in initial_children
        }
        if new_children:
            descriptions = "; ".join(
                f"pid={pid} state={state} comm={comm!r} cmdline={cmdline!r}"
                for (pid, _), (state, comm, cmdline) in new_children.items()
            )
            for (pid, _), _details in new_children.items():
                _stop_and_reap(pid)
            failures.append(f"new child processes: {descriptions}")
        return failures

    def test_in_process_run_restores_handlers_and_subreaper_and_leaves_nothing(self) -> None:
        loader = importlib.machinery.SourceFileLoader(
            "oryx_tunnel_key_under_test", str(self.test_file)
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        unrelated = subprocess.Popen(
            [self.python3, "-c", "import sys; sys.stdin.read(); sys.exit(7)"],
            stdin=subprocess.PIPE,
            text=True,
        )
        try:
            previous_handlers = {signum: signal.getsignal(signum) for signum in _SIGNALS}
            previous_subreaper = _child_subreaper()
            initial_children = _own_children()
            unrelated_children = [
                child for child in initial_children if child[0] == unrelated.pid
            ]
            self.assertEqual(
                len(unrelated_children),
                1,
                "unrelated child was not visible in /proc",
            )

            for label, patcher in (
                ("success", None),
                ("ssh-add failure", mock.patch.object(module, "SSH_ADD", Path("/usr/bin/false"))),
            ):
                result = unittest.TestResult()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", ResourceWarning)
                    if patcher is None:
                        module.OryxTunnelKey(_AGENT_TEST).run(result)
                    else:
                        with patcher:
                            module.OryxTunnelKey(_AGENT_TEST).run(result)

                failures: list[str] = []
                if result.testsRun != 1:
                    failures.append(f"expected one test run, got {result.testsRun}")
                if label == "success":
                    if not result.wasSuccessful():
                        failures.append(
                            f"successful in-process run failed: failures={result.failures!r} errors={result.errors!r}"
                        )
                else:
                    if len(result.failures) != 1 or result.errors:
                        failures.append(
                            f"expected one failure and no errors, got failures={result.failures!r} errors={result.errors!r}"
                        )
                    elif "added.returncode" not in result.failures[0][1]:
                        # The run failed before the ssh-add it was given to fail: say where, or the
                        # line names no cause (one full run on sami-devbox, 2026-11-16, passed alone).
                        failures.append("in-process failure did not contain added.returncode; it was:\n"
                                        + result.failures[0][1][-1500:])
                failures.extend(
                    self._post_run_failures(
                        initial_children, previous_handlers, previous_subreaper
                    )
                )
                with self.subTest(run=label):
                    self.assertFalse(failures, "\n".join(failures))

            self.assertIsNone(unrelated.poll(), "unrelated child exited during tunnel-key runs")
            assert unrelated.stdin is not None
            unrelated.stdin.close()
            self.assertEqual(unrelated.wait(timeout=5), 7)
        finally:
            if unrelated.stdin is not None and not unrelated.stdin.closed:
                unrelated.stdin.close()
            if unrelated.poll() is None:
                _stop_and_reap(unrelated.pid)
            else:
                try:
                    unrelated.wait(timeout=0)
                except subprocess.TimeoutExpired:
                    pass

    def test_signal_while_setup_installs_its_handler_reaches_the_previous_handler(self) -> None:
        """A SIGTERM that lands in the instant after setUp installs its handler for SIGTERM,
        before setUp has recorded the handler it replaced, must still reach the previous
        handler once, and the previous handler must be installed again after the run.

        The previous handler here records the signal instead of ending the process, so the
        run can be checked in this process. The trigger wraps signal.signal and sends the
        SIGTERM as soon as the real call has installed the test's handler; CPython runs the
        handler when that call returns, which is the point an outside SIGTERM can also reach.
        """
        loader = importlib.machinery.SourceFileLoader(
            "oryx_tunnel_key_install_gap", str(self.test_file)
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)

        received: list[int] = []

        def previous_handler(signum: int, frame: object) -> None:
            received.append(signum)

        real_signal = signal.signal
        fired: list[int] = []

        def install_then_signal(signum: int, handler: Any) -> Any:
            replaced = real_signal(signum, handler)
            if (
                signum == signal.SIGTERM
                and not fired
                and isinstance(getattr(handler, "__self__", None), module.OryxTunnelKey)
            ):
                fired.append(signum)
                os.kill(os.getpid(), signal.SIGTERM)
            return replaced

        saved = {signum: signal.getsignal(signum) for signum in _SIGNALS}
        previous_subreaper = _child_subreaper()
        initial_children = _own_children()
        real_signal(signal.SIGTERM, previous_handler)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ResourceWarning)
                with mock.patch.object(signal, "signal", install_then_signal):
                    module.OryxTunnelKey(_AGENT_TEST).run(unittest.TestResult())
            handler_after = signal.getsignal(signal.SIGTERM)
        finally:
            for signum, handler in saved.items():
                real_signal(signum, handler)
            # Record, then stop, what the run left, so a failing run (main's file) leaves nothing.
            new_children = [child for child in _own_children() if child not in initial_children]
            for pid, _ in new_children:
                _stop_and_reap(pid)

        self.assertEqual(
            fired, [signal.SIGTERM], "setUp never installed a SIGTERM handler of the test"
        )
        self.assertIs(
            handler_after,
            previous_handler,
            f"the SIGTERM handler after the run is {handler_after!r}, not the one before it",
        )
        self.assertEqual(
            received,
            [signal.SIGTERM],
            "the SIGTERM did not reach the handler that was installed before the test",
        )
        self.assertEqual(_child_subreaper(), previous_subreaper)
        self.assertEqual(new_children, [], "the run left a child process")


if __name__ == "__main__":
    unittest.main()
