#!/usr/bin/env python3
"""Regression coverage for browser-capture's non-interactive secretsd contract."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
CAPTURE = SCRIPTS / "browser-capture"


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/usr/bin/env python3\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class BrowserCaptureSecretsContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.stub_dir = Path(self.temp_dir.name)
        self.env = {**os.environ, "PATH": f"{self.stub_dir}:{os.environ['PATH']}"}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_driver(
        self, body: str, timeout: float = 1
    ) -> subprocess.CompletedProcess[str]:
        driver = textwrap.dedent(
            f"""
            import argparse
            import importlib.machinery
            import importlib.util
            import sys

            loader = importlib.machinery.SourceFileLoader("browser_capture", {str(CAPTURE)!r})
            spec = importlib.util.spec_from_loader(loader.name, loader)
            browser_capture = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = browser_capture
            loader.exec_module(browser_capture)
            {body}
            """
        )
        try:
            return subprocess.run(
                [sys.executable, "-c", driver],
                capture_output=True,
                text=True,
                env=self.env,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail("browser-capture did not terminate the stalled secrets process")

    def run_capture(
        self, *extra_args: str, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        env.pop("BROWSER_RELAY_URL", None)
        env.update(extra_env or {})
        return subprocess.run(
            [
                sys.executable,
                str(CAPTURE),
                "--domain",
                "example.test",
                "--cookie",
                "session",
                "--secret",
                "TEST_COOKIE",
                *extra_args,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
            check=False,
        )

    def test_incapable_secrets_stops_before_contacting_the_relay(self) -> None:
        """An editor-only secrets CLI is rejected before browser access begins."""
        write_stub(self.stub_dir, "secrets", "import time\ntime.sleep(60)")

        result = self.run_driver(
            """
            browser_capture.CAPABILITY_TIMEOUT_SECONDS = 0.05
            args = argparse.Namespace(relay_url="http://127.0.0.1:1", source="private")
            try:
                browser_capture.run(args)
            except browser_capture.CaptureError as error:
                print(f"{error.exit_code}:{error.message}")
            """
        )

        self.assertIn("7:browser-capture: secrets edit-human", result.stdout)
        self.assertIn("non-interactive stdin", result.stdout)
        self.assertNotIn("browser relay unreachable", result.stdout)

    def test_store_timeout_terminates_a_stalled_secrets_process(self) -> None:
        """A secrets child that keeps the cookie stdin open cannot stall capture."""
        write_stub(
            self.stub_dir,
            "secrets",
            "import sys, time\nsys.stdin.buffer.read()\ntime.sleep(60)",
        )

        result = self.run_driver(
            """
            browser_capture.STORE_TIMEOUT_SECONDS = 0.05
            browser_capture.response_for = lambda *_: {
                "cookies": [{
                    "name": "session",
                    "value": "BROWSER_CAPTURE_SENTINEL_VALUE",
                    "domain": ".x.test",
                    "path": "/",
                    "expires": -1,
                }]
            }
            try:
                browser_capture.store_matching_cookie(None, "x.test", "session", "K", "private", "S1")
            except browser_capture.CaptureError as error:
                print(f"{error.exit_code}:{error.message}")
            """
        )

        self.assertIn("7:browser-capture: secrets edit-human timed out", result.stdout)
        self.assertNotIn("BROWSER_CAPTURE_SENTINEL_VALUE", result.stdout)
        self.assertNotIn("BROWSER_CAPTURE_SENTINEL_VALUE", result.stderr)

    def test_relay_flag_supplies_the_relay_url(self) -> None:
        """Removing the flag must not make an explicit relay endpoint unreachable."""
        write_stub(
            self.stub_dir,
            "secrets",
            "import sys\n"
            "sys.stdin.buffer.read()\n"
            "sys.stderr.write(\"piped secret 'BROWSER_CAPTURE_STDIN_PROBE' value must not be empty\")\n"
            "sys.exit(1)",
        )

        result = self.run_capture("--relay-url", "http://127.0.0.1:9")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(
            "browser relay unreachable at http://127.0.0.1:9/json/version",
            result.stderr,
        )

    def test_relay_environment_supplies_the_relay_url(self) -> None:
        """Removing environment lookup must not discard the configured endpoint."""
        write_stub(
            self.stub_dir,
            "secrets",
            "import sys\n"
            "sys.stdin.buffer.read()\n"
            "sys.stderr.write(\"piped secret 'BROWSER_CAPTURE_STDIN_PROBE' value must not be empty\")\n"
            "sys.exit(1)",
        )

        result = self.run_capture(
            extra_env={"BROWSER_RELAY_URL": "http://127.0.0.1:10"}
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(
            "browser relay unreachable at http://127.0.0.1:10/json/version",
            result.stderr,
        )

    def test_missing_relay_url_stops_before_secrets_or_relay_access(self) -> None:
        """Adding a default URL would make unconfigured capture dial a relay."""
        secrets_called = self.stub_dir / "secrets-called"
        write_stub(
            self.stub_dir,
            "secrets",
            f"from pathlib import Path\nPath({str(secrets_called)!r}).touch()",
        )

        result = self.run_capture()

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(
            "supply --relay-url URL or set BROWSER_RELAY_URL",
            result.stderr,
        )
        self.assertFalse(secrets_called.exists())

    def test_matching_target_uses_relay_target_list(self) -> None:
        """Target selection reads the relay list and preserves URL/title narrowing."""
        result = self.run_driver(
            """
            import json
            from unittest.mock import patch

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

                def read(self):
                    return json.dumps([
                        {
                            "id": "one",
                            "title": "Pull requests",
                            "type": "page",
                            "url": "https://github.com/sjawhar/forward/pulls",
                        },
                        {
                            "id": "two",
                            "title": "Issues",
                            "type": "page",
                            "url": "https://api.github.com/repos/sjawhar/forward/issues",
                        },
                    ]).encode()

            seen_urls = []
            def urlopen(request, **_):
                seen_urls.append(request.full_url if hasattr(request, "full_url") else request)
                return Response()

            with patch.object(browser_capture.urllib.request, "urlopen", side_effect=urlopen):
                print(browser_capture.matching_target(
                    "http://relay.example/json/list",
                    "github.com",
                    "Pull Requests",
                ))
            print(seen_urls)
            """
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "('one', 'https://github.com/sjawhar/forward/pulls')\n"
            "['http://relay.example/json/list']\n",
        )

    def test_cdp_protocol_error_names_method_and_message(self) -> None:
        """A CDP error exposes only its method metadata and protocol message."""
        result = self.run_driver(
            """
            class Connection:
                def send(self, _):
                    pass

                def recv(self):
                    return '{"id": 1, "error": {"code": -32601, "message": "method unavailable"}}'

            try:
                browser_capture.response_for(Connection(), 1, "Target.getTargets", None)
            except browser_capture.CaptureError as error:
                print(f"{error.exit_code}:{error.message}")
            """
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "1:browser-capture: CDP command Target.getTargets failed: method unavailable\n",
        )


if __name__ == "__main__":
    unittest.main()
