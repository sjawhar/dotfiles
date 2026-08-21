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

    def run_driver(self, body: str, timeout: float = 1) -> subprocess.CompletedProcess[str]:
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
                    "value": "test-cookie-value",
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


if __name__ == "__main__":
    unittest.main()
