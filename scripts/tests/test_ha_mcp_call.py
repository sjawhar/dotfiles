#!/usr/bin/env python3
"""plugins/sjawhar/skills/home-assistant/ha-mcp-call.sh: the tool call reaches HA_MCP_URL, whose
webhook id is the credential, without the URL ever sitting in curl's argv, which every user can
read from `/proc/<pid>/cmdline`.

Technique: a recording `curl` first on PATH writes its argv, NUL-separated, then execs the real
curl, which talks to a local listener standing in for the ha-mcp webhook; the listener records
each request and answers one tools/call result as an SSE `data:` line, as ha-mcp does.
"""

from __future__ import annotations

import json
import stat
import subprocess
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
HA_MCP_CALL = DOTFILES / "plugins" / "sjawhar" / "skills" / "home-assistant" / "ha-mcp-call.sh"

CURL_RECORDER = r"""#!/bin/sh
for a in "$@"; do printf '%s\0' "$a" >> "$CURL_LOG"; done
exec /usr/bin/curl "$@"
"""


class Webhook(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, dict]] = []

    def do_POST(self) -> None:  # noqa: N802 (http.server's name)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append((self.command, self.path, body))
        result = {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "tool ran"}]}}
        payload = f"event: message\ndata: {json.dumps(result)}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_: object) -> None:
        pass


class HaMcpCall(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        recorder = root / "recorder"
        recorder.mkdir()
        (recorder / "curl").write_text(CURL_RECORDER, encoding="utf-8")
        (recorder / "curl").chmod(stat.S_IRWXU)
        self.curl_log = root / "curl.argv"
        Webhook.requests = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Webhook)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.fake_id = f"fake-{uuid.uuid4().hex}"
        self.env = {
            "PATH": f"{recorder}:/usr/bin:/bin",
            "HOME": str(root),
            "CURL_LOG": str(self.curl_log),
            "HA_MCP_URL": f"http://127.0.0.1:{self.httpd.server_address[1]}/api/webhook/{self.fake_id}",
        }

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.temp_dir.cleanup()

    def test_the_call_reaches_the_webhook_and_its_url_stays_out_of_argv(self) -> None:
        result = subprocess.run(
            [str(HA_MCP_CALL), "ha_get_state", '{"entity_id": "light.office"}'],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "tool ran\n")
        self.assertEqual(len(Webhook.requests), 1)
        method, path, body = Webhook.requests[0]
        self.assertEqual((method, path), ("POST", f"/api/webhook/{self.fake_id}"))
        self.assertEqual(body["params"], {"name": "ha_get_state", "arguments": {"entity_id": "light.office"}})
        argv = self.curl_log.read_text(encoding="utf-8").split("\0")[:-1]
        self.assertTrue(argv, "curl was not called through the recorder")
        self.assertFalse([a for a in argv if self.fake_id in a], argv)


if __name__ == "__main__":
    unittest.main()
