#!/usr/bin/env python3
"""scripts/envoy send and notify: who the message says it is from.

With no flag the sender is derived from the environment: an agent harness's session marker
stamps source=agent and that session's id as source_session. A leading `--source envoy` stamps
source=envoy and never a source_session, whatever the environment holds, so an automated notice
does not show as the session that happened to run it (the inbox renders the sender as
`source_session ?? source`). envoy is the only value a caller may assert. `notify` is that send,
for the timers that post such notices.

Technique: stub `curl` first on PATH; it records its argv NUL-separated and answers as the
listener would, so each test reads back the URL and the JSON body the script posted.
"""

from __future__ import annotations

import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
ENVOY = DOTFILES / "scripts" / "envoy"
ENVOY_URL = "http://envoy.test:9020"
TOPIC = "notifications.role.example-watch"

CURL_STUB = r"""
printf '%s\0' "$@" > "$CURL_LOG"
echo '{"ok":true}'
"""


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class EnvoySend(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        stub_dir = root / "bin"
        stub_dir.mkdir()
        write_stub(stub_dir, "curl", CURL_STUB)
        self.curl_log = root / "curl.argv"
        # Built from scratch, so no session marker of the session running the tests leaks in.
        self.env = {
            "PATH": f"{stub_dir}:/usr/bin:/bin",
            "HOME": str(root),
            "HOSTNAME": "example-host-test",
            "ENVOY_URL": ENVOY_URL,
            "CURL_LOG": str(self.curl_log),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def send(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return self.envoy("send", *args, **extra)

    def envoy(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ENVOY), *args],
            capture_output=True,
            text=True,
            env={**self.env, **extra},
            check=False,
            timeout=30,
        )

    def posted(self) -> tuple[str, dict[str, str]]:
        argv = self.curl_log.read_text(encoding="utf-8").split("\0")[:-1]
        url = argv[argv.index("POST") + 1]
        body = json.loads(argv[argv.index("-d") + 1])
        return url, body

    def test_source_envoy_publishes_as_envoy_without_a_session(self) -> None:
        result = self.send("--source", "envoy", TOPIC, "daily notice")
        self.assertEqual(result.returncode, 0, result.stderr)
        url, body = self.posted()
        self.assertEqual(url, f"{ENVOY_URL}/v1/messages/publish")
        self.assertEqual(body, {"topic": TOPIC, "message": "daily notice", "source": "envoy"})

    def test_source_envoy_drops_an_inherited_session_id(self) -> None:
        for marker in ("OMP_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
            with self.subTest(marker=marker):
                result = self.send("--source", "envoy", TOPIC, "daily notice", **{marker: "x"})
                self.assertEqual(result.returncode, 0, result.stderr)
                _, body = self.posted()
                self.assertEqual(body["source"], "envoy")
                self.assertNotIn("source_session", body)

    def test_source_refuses_any_value_but_envoy(self) -> None:
        result = self.send("--source", "human", TOPIC, "daily notice")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("--source accepts only envoy", result.stderr)
        self.assertFalse(self.curl_log.exists(), "nothing may be posted")

    def test_without_the_flag_an_agent_session_sends_as_itself(self) -> None:
        result = self.send(TOPIC, "hello", OMP_SESSION_ID="x")
        self.assertEqual(result.returncode, 0, result.stderr)
        url, body = self.posted()
        self.assertEqual(url, f"{ENVOY_URL}/v1/messages/publish")
        self.assertEqual(body["source"], "agent")
        self.assertEqual(body["source_session"], "x")

    def test_notify_publishes_as_envoy_whatever_session_runs_it(self) -> None:
        result = self.envoy("notify", TOPIC, "daily notice", OMP_SESSION_ID="x")
        self.assertEqual(result.returncode, 0, result.stderr)
        url, body = self.posted()
        self.assertEqual(url, f"{ENVOY_URL}/v1/messages/publish")
        self.assertEqual(body, {"topic": TOPIC, "message": "daily notice", "source": "envoy"})

    def test_notify_needs_a_topic_and_a_message(self) -> None:
        for args in ((TOPIC,), (TOPIC, "a", "b")):
            with self.subTest(args=args):
                result = self.envoy("notify", *args)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("Usage: envoy notify <topic> <message>", result.stderr)
                self.assertFalse(self.curl_log.exists(), "nothing may be posted")


if __name__ == "__main__":
    unittest.main()
