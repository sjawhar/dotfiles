#!/usr/bin/env python3
"""plugins/sjawhar/skills/home-assistant/ha-mcp-call.sh sends a tool call to HA_MCP_URL, whose
webhook id is a credential, without the URL ever sitting in curl's argv, which every user can read
from `/proc/<pid>/cmdline`.

Technique: a recording `curl` first on PATH writes its argv, NUL-separated, then execs the real
curl, which talks to a local listener standing in for the ha-mcp webhook. Each test sets that
listener's canned response and checks the script's response handling.
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


def sse(message: dict[str, object]) -> bytes:
    return f"event: message\ndata: {json.dumps(message)}\n\n".encode()


class Webhook(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, dict[str, str], dict[str, object]]] = []

    def do_POST(self) -> None:  # noqa: N802 (http.server's name)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append((self.command, self.path, dict(self.headers.items()), body))
        payload = self.server.response_body
        self.send_response(self.server.response_status)
        self.send_header("Content-Type", self.server.response_content_type)
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
        self.set_response()
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

    def set_response(
        self,
        *,
        status: int = 200,
        content_type: str = "text/event-stream",
        body: bytes = b"",
    ) -> None:
        self.httpd.response_status = status
        self.httpd.response_content_type = content_type
        self.httpd.response_body = body

    def run_call(
        self,
        *,
        tool: str = "ha_get_state",
        arguments: str = '{"entity_id": "light.office"}',
        env: dict[str, str] | None = None,
        token: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        call_env = self.env | (env or {})
        if token is not None:
            call_env["HA_MCP_TOKEN"] = token
        return subprocess.run(
            [str(HA_MCP_CALL), tool, arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=call_env,
            timeout=30,
        )

    def test_the_call_reaches_the_webhook_and_its_url_stays_out_of_argv(self) -> None:
        result_message = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": "tool ran"}]},
        }
        self.set_response(body=sse(result_message))
        result = self.run_call()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "tool ran\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(Webhook.requests), 1)
        method, path, _, body = Webhook.requests[0]
        self.assertEqual((method, path), ("POST", f"/api/webhook/{self.fake_id}"))
        self.assertEqual(body["params"], {"name": "ha_get_state", "arguments": {"entity_id": "light.office"}})
        argv = self.curl_log.read_text(encoding="utf-8").split("\0")[:-1]
        self.assertTrue(argv, "curl was not called through the recorder")
        self.assertFalse([argument for argument in argv if self.fake_id in argument], argv)

    def test_sse_success_prints_each_text_content(self) -> None:
        result_message = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [
                    {"type": "text", "text": "first result"},
                    {"type": "text", "text": "second result"},
                ]
            },
        }
        self.set_response(body=sse(result_message))
        result = self.run_call()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "first result\nsecond result\n")
        self.assertEqual(result.stderr, "")

    def test_json_success_prints_text_content(self) -> None:
        result_message = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": "JSON result"}]},
        }
        self.set_response(
            content_type="application/json",
            body=json.dumps(result_message).encode(),
        )
        result = self.run_call()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "JSON result\n")
        self.assertEqual(result.stderr, "")

    def test_sse_is_error_writes_text_to_stderr(self) -> None:
        result_message = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": "Unknown tool: 'no_such_tool'"}],
            },
        }
        self.set_response(body=sse(result_message))
        result = self.run_call(tool="no_such_tool")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Unknown tool: 'no_such_tool'", result.stderr)

    def test_json_is_error_writes_text_to_stderr(self) -> None:
        result_message = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": "Unknown tool: 'no_such_tool'"}],
            },
        }
        self.set_response(
            content_type="application/json",
            body=json.dumps(result_message).encode(),
        )
        result = self.run_call(tool="no_such_tool")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Unknown tool: 'no_such_tool'", result.stderr)

    def test_sse_error_writes_message_to_stderr(self) -> None:
        error_message = {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32602, "message": "bad params"},
        }
        self.set_response(body=sse(error_message))
        result = self.run_call()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("bad params", result.stderr)

    def test_sse_success_preserves_raw_json_line_separators(self) -> None:
        for character in ("\u2028", "\u0085", "\u2029"):
            with self.subTest(character=f"U+{ord(character):04X}"):
                text = f"before{character}after"
                result_message = {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"content": [{"type": "text", "text": text}]},
                }
                self.set_response(
                    body=f"data: {json.dumps(result_message, ensure_ascii=False)}\n\n".encode()
                )
                result = self.run_call()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"{text}\n")
                self.assertEqual(result.stderr, "")

    def test_sse_error_preserves_raw_json_line_separator_in_message(self) -> None:
        message = "bad\u2028params"
        error_message = {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32602, "message": message},
        }
        self.set_response(
            body=f"data: {json.dumps(error_message, ensure_ascii=False)}\n\n".encode()
        )
        result = self.run_call()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn(message, result.stderr)

    def test_id_2_without_a_result_or_error_fails(self) -> None:
        responses = (
            (
                "json",
                "application/json",
                {"jsonrpc": "2.0", "id": 2},
            ),
            (
                "event stream",
                "text/event-stream",
                {"jsonrpc": "2.0", "id": 2, "result": None},
            ),
        )
        for name, content_type, message in responses:
            with self.subTest(response=name):
                body = (
                    json.dumps(message).encode()
                    if content_type == "application/json"
                    else sse(message)
                )
                self.set_response(content_type=content_type, body=body)
                result = self.run_call()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("neither result nor error", result.stderr)

    def test_empty_result_succeeds_without_output(self) -> None:
        result_message = {"jsonrpc": "2.0", "id": 2, "result": {}}
        self.set_response(body=sse(result_message))
        result = self.run_call()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_content_that_is_not_a_list_fails(self) -> None:
        result_message = {"jsonrpc": "2.0", "id": 2, "result": {"content": "tool ran"}}
        self.set_response(body=sse(result_message))
        result = self.run_call()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("content of the result is not a list", result.stderr)

    def test_empty_event_stream_has_no_answer(self) -> None:
        self.set_response(body=b"")
        result = self.run_call()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("no answer", result.stderr)

    def test_event_stream_with_only_another_id_has_no_answer(self) -> None:
        other_message = {"jsonrpc": "2.0", "id": 1, "result": {}}
        self.set_response(body=sse(other_message))
        result = self.run_call()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("no answer", result.stderr)

    def test_cut_event_stream_answer_has_no_answer(self) -> None:
        self.set_response(body=b'data: {"jsonrpc":"2.0","id":2,"res')
        result = self.run_call()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("no answer", result.stderr)

    def test_priming_event_with_empty_data_is_skipped(self) -> None:
        # MCP 2025-11-25, Streamable HTTP: a server that opens an SSE stream SHOULD first send an
        # event with an id and an empty data field, to prime reconnection. It carries no message,
        # so the answer that follows it is still the answer.
        result_message = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": "tool ran"}]},
        }
        for priming in (b"id: 0\ndata:\n\n", b"id: 0\ndata: \n\n"):
            with self.subTest(priming=priming):
                self.set_response(body=priming + sse(result_message))
                result = self.run_call()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "tool ran\n")
                self.assertEqual(result.stderr, "")

    def test_bearer_token_reaches_webhook_but_not_curl_argv(self) -> None:
        result_message = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": "tool ran"}]},
        }
        token = f"fake-token-{uuid.uuid4()}"
        self.set_response(body=sse(result_message))
        result = self.run_call(token=token)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "tool ran\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(Webhook.requests), 1)
        _, _, headers, _ = Webhook.requests[0]
        self.assertEqual(headers.get("Authorization"), f"Bearer {token}")
        argv = self.curl_log.read_text(encoding="utf-8").split("\0")[:-1]
        self.assertTrue(argv, "curl was not called through the recorder")
        self.assertFalse([argument for argument in argv if token in argument], argv)
        self.assertFalse([argument for argument in argv if self.fake_id in argument], argv)

    def test_no_authorization_header_without_a_token_or_with_an_empty_token(self) -> None:
        result_message = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": "tool ran"}]},
        }
        self.set_response(body=sse(result_message))
        for token in (None, ""):
            with self.subTest(token=token):
                result = self.run_call(token=token)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "tool ran\n")
                self.assertEqual(result.stderr, "")
                _, _, headers, _ = Webhook.requests[-1]
                self.assertNotIn("Authorization", headers)

    def test_http_401_uses_curl_error_without_a_no_answer_message(self) -> None:
        self.set_response(
            status=401,
            content_type="application/json",
            body=b'{"error":"unauthorized"}',
        )
        result = self.run_call()
        self.assertEqual(result.returncode, 22, "curl --fail's own status for an HTTP error")
        self.assertEqual(result.stdout, "")
        self.assertIn("401", result.stderr)
        self.assertNotIn("no answer", result.stderr)


if __name__ == "__main__":
    unittest.main()
