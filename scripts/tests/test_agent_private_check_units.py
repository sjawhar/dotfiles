#!/usr/bin/env python3
"""The parts of scripts/agent-private-check, each tested alone: its HTTP layer, its cache writes,
its token commands, whoami's answer, and a resumed session's directory.

Technique: each case loads the check as a module from a copy in its own scratch directory
(private_check_support.load_check), with a scratch HOME, and starts only the stand-in servers it
needs. The launchers that run the check are tested in test_agent_private_check.py.
"""

from __future__ import annotations

import contextlib
import http.client
import http.server
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from private_check_support import executable, load_check
from private_project_stubs import PRIV_TOKEN, PUB_TOKEN, WHOAMI, DispatchStub


class Unit(unittest.TestCase):
    tmp: Path
    home: Path
    check: Any  # the check as a module: a case sets its constants

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="agent-private-check-unit-")
        self.addCleanup(scratch.cleanup)
        self.tmp = Path(scratch.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.check = load_check(self.tmp)

    def dispatch(self, answers: dict[str, Any] | None = None, routes: dict[str, Any] | None = None) -> DispatchStub:
        server = DispatchStub(answers, routes)
        self.addCleanup(server.stop)
        return server

    def environment(self, **extra: str) -> contextlib.AbstractContextManager[object]:
        """The check's view of a session: this case's HOME and cache, no bearer but EXTRA's."""
        return mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_CACHE_HOME": str(self.home / ".cache"),
                                            "XDG_CONFIG_HOME": str(self.home / ".config"), "DISPATCH_TOKEN_FILE": "",
                                            "DISPATCH_TOKEN": "", "DISPATCH_URL": "", "ENVOY_CONFIG_FILE": "",
                                            "AGENT_PRIVATE_HARNESS": "", "AGENT_PRIVATE_CWD": str(self.tmp), **extra})


class TheHttpLayer(Unit):
    def test_invalid_http_responses_are_unanswered_without_secret_bytes(self) -> None:
        check = self.check
        with mock.patch.object(check.urllib.request.OpenerDirector, "open",
                               side_effect=http.client.IncompleteRead(PRIV_TOKEN.encode())):
            with self.assertRaises(check.NoAnswer) as failed:
                check.fetch("http://unused.test/", proxy=False)
        self.assertNotIn(PRIV_TOKEN, str(failed.exception))

    def test_dns_resolution_obeys_the_http_deadline(self) -> None:
        check = self.check
        check.TIMEOUT_S = 0.05
        with mock.patch("socket.getaddrinfo", side_effect=lambda *a, **k: time.sleep(0.5)):
            with self.assertRaises(check.NoAnswer):
                check.fetch("http://lookup.invalid/", proxy=False)

    def test_a_slow_response_body_obeys_the_http_deadline(self) -> None:
        check = self.check
        check.TIMEOUT_S = 0.05

        class SlowBody:
            def read(self, size: int = -1) -> bytes:
                time.sleep(0.5)
                return b"a body that outlived the request deadline"

        with mock.patch.object(check.urllib.request.OpenerDirector, "open",
                               return_value=contextlib.nullcontext(SlowBody())):
            with self.assertRaises(check.NoAnswer):
                check.fetch("http://unused.test/", proxy=False)

    def test_an_answer_larger_than_64_kib_is_no_answer(self) -> None:
        server = self.dispatch(routes={"/big": {"pad": "x" * (64 * 1024)}, "/small": {"pad": "x" * 60000}})
        with self.assertRaisesRegex(self.check.NoAnswer, "larger than 65536 bytes"):
            self.check.fetch(server.url + "/big", proxy=False)
        # Control: an answer under the bound is read whole.
        self.assertEqual(len(json.loads(self.check.fetch(server.url + "/small", proxy=False))["pad"]), 60000)

    def test_a_redirect_does_not_forward_the_bearer(self) -> None:
        check = self.check
        dispatch = self.dispatch({PUB_TOKEN: {"kind": "agent"}})
        destination = dispatch.url + WHOAMI
        # Control: the destination can see this bearer when contacted directly.
        check.fetch(destination, {"Authorization": f"Bearer {PUB_TOKEN}"}, proxy=False)
        self.assertEqual(dispatch.paths(), [WHOAMI])
        dispatch.calls.clear()

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server's name
                self.send_response(302)
                self.send_header("Location", destination)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - http.server's name
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(check.NoAnswer):
                check.fetch(f"http://127.0.0.1:{server.server_port}/", {"Authorization": f"Bearer {PUB_TOKEN}"}, proxy=False)
            self.assertEqual(dispatch.paths(), [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class WhoamisAnswer(Unit):
    def answer(self, body: dict[str, Any]) -> list[str]:
        dispatch = self.dispatch({PUB_TOKEN: body})
        return self.check.whoami(dispatch.url, PUB_TOKEN)

    def test_only_project_keys_are_taken_from_an_answer(self) -> None:
        for keys in (["\x1b]0;pwned\x07"], ["priv"], ["PRIV-1"], ["9LIVES"], [""]):
            with self.subTest(keys=keys), self.assertRaisesRegex(self.check.NoAnswer, "not a list of project keys"):
                self.answer({"kind": "agent", "projects": keys, "private_projects": keys})
        # Control: keys as Dispatch writes them, sorted.
        self.assertEqual(self.answer({"kind": "agent", "projects": ["ZZ9", "PRIV"], "private_projects": ["ZZ9", "PRIV"]}),
                         ["PRIV", "ZZ9"])

    def test_an_answer_naming_a_project_without_private_projects_is_no_answer(self) -> None:
        # Today's whoami names a scoped bearer's `project` and no lists at all.
        for body in ({"kind": "agent", "projects": ["PUB"]},
                     {"kind": "agent", "owner": "o", "project": "PRIV", "scopes": [], "service": None, "session": None}):
            with self.subTest(body=body), self.assertRaisesRegex(self.check.NoAnswer, "no private_projects"):
                self.answer(body)
        # Controls: both lists, an answer naming no project at all, and Go's nil slice.
        self.assertEqual(self.answer({"kind": "agent", "projects": ["PUB"], "private_projects": []}), [])
        self.assertEqual(self.answer({"kind": "agent"}), [])
        self.assertEqual(self.answer({"kind": "agent", "owner": "o", "project": None, "scopes": []}), [])
        self.assertEqual(self.answer({"kind": "agent", "projects": ["PUB"], "private_projects": None}), [])

    def test_a_kept_refusal_holds_only_project_keys(self) -> None:
        kept = self.tmp / "kept"
        kept.write_text(json.dumps({"private_projects": ["\x1b[2J"]}))
        self.assertEqual(self.check.kept_refusal(kept), [])
        kept.write_text(json.dumps({"private_projects": ["PRIV"]}))
        self.assertEqual(self.check.kept_refusal(kept), ["PRIV"])


class AResumedSession(Unit):
    ID = "01a1f00d-1234-7abc-8def-0123456789ab"

    def session(self, name: str, *lines: dict[str, Any]) -> Path:
        path = self.home / ".omp" / "agent" / "sessions" / "-somewhere" / f"2026-11-21T00-00-00-000Z_{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        return path

    def test_the_session_files_cwd_is_its_directory(self) -> None:
        agent = self.home / ".omp" / "agent"
        file = self.session(self.ID, {"type": "title", "title": "t"},
                            {"type": "session", "id": self.ID, "cwd": "/srv/checkout"}, {"type": "model_change"})
        for target in (str(file), self.ID, self.ID[:13]):
            with self.subTest(target=target):
                self.assertEqual(self.check.resumed_cwd(target, self.tmp, agent), Path("/srv/checkout"))

    def test_what_cannot_be_resolved_is_none(self) -> None:
        agent = self.home / ".omp" / "agent"
        self.session(self.ID, {"type": "session", "cwd": "/srv/a"})
        self.session(self.ID[:8] + "-ffff-7abc-8def-0123456789ab", {"type": "session", "cwd": "/srv/b"})
        for target in (self.ID[:8], "not-an-id", "--model", str(self.tmp / "missing.jsonl")):
            with self.subTest(target=target):
                self.assertIsNone(self.check.resumed_cwd(target, self.tmp, agent))
        relative = self.session("relative", {"type": "session", "cwd": "srv/a"})
        self.assertIsNone(self.check.resumed_cwd(str(relative), self.tmp, agent))


class TheCacheAndFaults(Unit):
    def test_a_failed_cache_write_and_cleanup_are_both_optional(self) -> None:
        with mock.patch.object(Path, "write_bytes", side_effect=PermissionError), \
                mock.patch.object(Path, "unlink", side_effect=PermissionError):
            self.check.keep(self.tmp / "copy", b'{"enabled": false}')

    def test_an_unexpected_exception_does_not_print_credentials(self) -> None:
        output = io.StringIO()
        with mock.patch.object(self.check, "check", side_effect=ValueError(PRIV_TOKEN)), contextlib.redirect_stdout(output):
            self.assertEqual(self.check.check_main([]), 0)
        self.assertTrue(output.getvalue().startswith("refuse "))
        self.assertNotIn(PRIV_TOKEN, output.getvalue())


class TokenCommands(Unit):
    def config(self, directory: Path, token: str, server: str) -> Path:
        path = directory / "envoy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"dispatch": {"enabled": True, "serverUrl": server, "token": token}}))
        return path

    def test_a_token_command_is_not_put_on_a_process_argument(self) -> None:
        dispatch = self.dispatch()
        config = self.config(self.home / ".config" / "legion", f"!printf '%s' '{PRIV_TOKEN}'", dispatch.url)
        real_popen = subprocess.Popen
        arguments = []

        def popen(argv, *args, **kwargs):
            arguments.append(argv)
            return real_popen(argv, *args, **kwargs)

        with self.environment(AGENT_PRIVATE_ENVOY_JSON=str(config)), \
                mock.patch.object(Path, "cwd", return_value=self.tmp), \
                mock.patch.object(self.check.subprocess, "Popen", side_effect=popen):
            self.assertEqual(self.check.bearers(), [(dispatch.url, PRIV_TOKEN, f"dispatch.token in {config}")])
        self.assertTrue(arguments)
        self.assertNotIn(PRIV_TOKEN, repr(arguments))

    def test_a_token_commands_background_child_cannot_hold_the_launch_open(self) -> None:
        check = self.check
        check.COMMAND_TIMEOUT_S = 0.05
        real_popen = subprocess.Popen
        real_killpg = os.killpg

        def exists(pid: int) -> bool:
            # Reap it ourselves if the test runner is a subreaper; otherwise init does so.
            with contextlib.suppress(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            return True

        def await_gone(pid: int) -> None:
            deadline = time.monotonic() + 5
            while exists(pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(exists(pid), f"token-command child {pid} still exists")

        # Run the blind implementation first: killing only the shell must leave its child alive.
        for shell_only in (True, False):
            with self.subTest(shell_only=shell_only):
                directory = self.tmp / ("shell-only" if shell_only else "whole-group")
                directory.mkdir()
                config = self.config(directory, "!./token-child & wait", "http://127.0.0.1:9")
                started = directory / "child-started"
                executable(directory / "token-child", f"#!{sys.executable}\n"
                           "import os, time\nfrom pathlib import Path\n"
                           "marker = Path('child-started.tmp')\n"
                           "marker.write_text(str(os.getpid()))\n"
                           "marker.replace('child-started')\ntime.sleep(60)\n")
                processes = []

                def spawn(*args, **kwargs):
                    process = real_popen(*args, **kwargs)
                    processes.append(process)
                    # The production timeout begins only after the child has actually started.
                    deadline = time.monotonic() + 10
                    while not started.is_file() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(started.is_file(), "the token-command child never started")
                    return process

                kill = (lambda pid, sig: os.kill(pid, sig)) if shell_only else real_killpg
                try:
                    with self.environment(AGENT_PRIVATE_ENVOY_JSON=str(config)), \
                            mock.patch.object(Path, "cwd", return_value=self.tmp), \
                            mock.patch.object(check.subprocess, "Popen", side_effect=spawn), \
                            mock.patch.object(check.os, "killpg", side_effect=kill):
                        with self.assertRaises(check.Refused):
                            check.bearers()
                    pid = int(started.read_text())
                    if shell_only:
                        self.assertTrue(exists(pid), "the shell-only control did not leave a live child")
                    else:
                        await_gone(pid)
                finally:
                    for process in processes:
                        with contextlib.suppress(ProcessLookupError):
                            real_killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    if started.is_file():
                        await_gone(int(started.read_text()))


if __name__ == "__main__":
    unittest.main()
