#!/usr/bin/env python3
"""Regression tests for scripts/envoy_client, the one Envoy client the Python watches share.

Eight watches each carried a copy of this class before 2026-11-07, so nothing proved the
resolution rule once: each copy was read against scripts/envoy by eye. These are that proof --
which envoy.json is read, which variable overrides which field, what a `!` token command may
and may not do, and what each of send() and publish() puts on the wire.

Technique: a real listener on 127.0.0.1 recording every request, because the whole point of the
class is the bytes it sends; only the token command is stubbed, with a shell script.
"""

import importlib.machinery
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(os.environ.get("ENVOY_CLIENT_SCRIPT") or Path(__file__).resolve().parents[1] / "envoy_client.py")
LOADER = importlib.machinery.SourceFileLoader("envoy_client_under_test", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
CLIENT = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(CLIENT)

# Every variable the class reads, cleared before each case so the machine's own does not leak in.
ENVOY_VARIABLES = ("ENVOY_CONFIG_FILE", "ENVOY_URL", "ENVOY_TOKEN", "XDG_CONFIG_HOME")


class Listener:
    """A listener that records each request and answers what the case asked it to."""

    def __init__(self, status: int = 200, body: object = None) -> None:
        self.status = status
        self.body: object = {"event_id": "e1"} if body is None else body
        self.seen: list[dict] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode() if length else ""
                recorder.seen.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "body": json.loads(raw) if raw else None,
                        "content_type": self.headers.get("Content-Type"),
                        "authorization": self.headers.get("Authorization"),
                    }
                )
                payload = json.dumps(recorder.body).encode()
                self.send_response(recorder.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _record
            do_POST = _record

            def log_message(self, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class EnvoyClientCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="envoy-client-test-"))
        self.addCleanup(self._clean)
        self.saved = {name: os.environ.pop(name, None) for name in ENVOY_VARIABLES}
        self.said: list[str] = []

    def _clean(self) -> None:
        for name, value in getattr(self, "saved", {}).items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def write_config(self, name: str, section: object) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"envoy": section}))
        return path

    def listener(self, **kwargs: object) -> Listener:
        made = Listener(**kwargs)  # type: ignore[arg-type]
        self.addCleanup(made.close)
        return made

    def client(self, **kwargs: object) -> object:
        return CLIENT.Envoy(log=self.said.append, **kwargs)  # type: ignore[arg-type]


class Resolution(EnvoyClientCase):
    def test_the_listener_defaults_to_the_local_one(self) -> None:
        os.environ["XDG_CONFIG_HOME"] = str(self.root / "empty")
        self.assertEqual(self.client().url, CLIENT.DEFAULT_ENVOY_URL)
        self.assertEqual(CLIENT.DEFAULT_ENVOY_URL, "http://127.0.0.1:9020")

    def test_the_xdg_config_home_file_is_read(self) -> None:
        self.write_config("legion/envoy.json", {"enabled": True, "url": "http://xdg.test"})
        os.environ["XDG_CONFIG_HOME"] = str(self.root)
        self.assertEqual(self.client().url, "http://xdg.test")

    def test_envoy_config_file_wins_over_xdg_config_home(self) -> None:
        self.write_config("legion/envoy.json", {"enabled": True, "url": "http://xdg.test"})
        handed = self.write_config("handed.json", {"enabled": True, "url": "http://handed.test"})
        os.environ["XDG_CONFIG_HOME"] = str(self.root)
        os.environ["ENVOY_CONFIG_FILE"] = str(handed)
        self.assertEqual(self.client().url, "http://handed.test")

    def test_envoy_url_wins_over_the_file_and_a_trailing_slash_goes(self) -> None:
        handed = self.write_config("handed.json", {"enabled": True, "url": "http://file.test"})
        os.environ["ENVOY_CONFIG_FILE"] = str(handed)
        os.environ["ENVOY_URL"] = "http://variable.test/"
        self.assertEqual(self.client().url, "http://variable.test")

    def test_a_file_that_turns_envoy_off_stops_the_caller(self) -> None:
        handed = self.write_config("off.json", {"enabled": False, "url": "http://file.test"})
        os.environ["ENVOY_CONFIG_FILE"] = str(handed)
        # Even with a listener named outright: the file disowns it, so nothing may be sent.
        os.environ["ENVOY_URL"] = "http://variable.test"
        with self.assertRaises(SystemExit) as refused:
            self.client()
        self.assertIn("sets envoy.enabled to false", str(refused.exception))

    def test_an_unreadable_or_broken_file_falls_back_rather_than_raising(self) -> None:
        for reason, content in (("missing", None), ("not json", "{"), ("no envoy section", "{}")):
            with self.subTest(reason=reason):
                path = self.root / "broken.json"
                path.unlink(missing_ok=True)
                if content is not None:
                    path.write_text(content)
                os.environ["ENVOY_CONFIG_FILE"] = str(path)
                self.assertEqual(self.client().url, CLIENT.DEFAULT_ENVOY_URL)


class Bearer(EnvoyClientCase):
    def command(self, body: str) -> str:
        path = self.root / "token-command"
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
        return f"!{path}"

    def test_a_plain_token_comes_from_the_file_and_the_variable_wins(self) -> None:
        handed = self.write_config("c.json", {"enabled": True, "token": "file-token"})
        os.environ["ENVOY_CONFIG_FILE"] = str(handed)
        self.assertEqual(self.client().token, "file-token")
        os.environ["ENVOY_TOKEN"] = "variable-token"
        self.assertEqual(self.client().token, "variable-token")

    def test_a_bang_token_is_a_command_whose_stdout_is_the_token(self) -> None:
        handed = self.write_config("c.json", {"enabled": True, "token": self.command("echo '  minted  '")})
        os.environ["ENVOY_CONFIG_FILE"] = str(handed)
        self.assertEqual(self.client().token, "minted")

    def test_a_command_that_fails_leaves_no_token_rather_than_a_wrong_one(self) -> None:
        for reason, body in (
            ("non-zero exit", "echo would-be-token; exit 1"),
            ("prints nothing", "exit 0"),
        ):
            with self.subTest(reason=reason):
                handed = self.write_config("c.json", {"enabled": True, "token": self.command(body)})
                os.environ["ENVOY_CONFIG_FILE"] = str(handed)
                self.assertEqual(self.client().token, "")

    def test_a_command_that_cannot_run_leaves_no_token(self) -> None:
        handed = self.write_config("c.json", {"enabled": True, "token": f"!{self.root}/not-here"})
        os.environ["ENVOY_CONFIG_FILE"] = str(handed)
        self.assertEqual(self.client().token, "")

    def test_neither_of_the_commands_streams_is_printed(self) -> None:
        """The token is on one of them, so a watch's log must never carry either."""
        handed = self.write_config(
            "c.json", {"enabled": True, "token": self.command("echo secret-token; echo noise >&2")}
        )
        os.environ["ENVOY_CONFIG_FILE"] = str(handed)
        with open(os.devnull, "w") as sink:
            import contextlib

            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                made = self.client()
        self.assertEqual(made.token, "secret-token")
        self.assertEqual(self.said, [])

    def test_the_bearer_rides_the_authorization_header_only_when_there_is_one(self) -> None:
        heard = self.listener()
        os.environ["ENVOY_URL"] = heard.url
        os.environ["ENVOY_TOKEN"] = "a-token"
        self.client().send("01a1-session", "hello")
        os.environ.pop("ENVOY_TOKEN")
        self.client().send("01a1-session", "hello")
        self.assertEqual(
            [row["authorization"] for row in heard.seen], ["Bearer a-token", None]
        )


class Transport(EnvoyClientCase):
    def setUp(self) -> None:
        super().setUp()
        self.heard = self.listener()
        os.environ["ENVOY_URL"] = self.heard.url

    def test_send_posts_the_session_message_and_source(self) -> None:
        self.assertTrue(self.client().send("01a1-session", "a message"))
        self.assertEqual(
            self.heard.seen,
            [
                {
                    "method": "POST",
                    "path": "/v1/messages/send",
                    "body": {"target_session": "01a1-session", "message": "a message", "source": "envoy"},
                    "content_type": "application/json",
                    "authorization": None,
                }
            ],
        )
        self.assertEqual(self.said, [])

    def test_publish_posts_the_topic_message_and_source(self) -> None:
        self.assertTrue(self.client().publish("notifications.role.lead-box-health", "a notice"))
        sent = self.heard.seen[0]
        self.assertEqual(sent["path"], "/v1/messages/publish")
        self.assertEqual(
            sent["body"],
            {"topic": "notifications.role.lead-box-health", "message": "a notice", "source": "envoy"},
        )

    def test_a_get_carries_no_body_and_no_content_type(self) -> None:
        status, body = self.client().request("GET", "/v1/sessions")
        self.assertEqual(status, 200)
        self.assertEqual(self.heard.seen[0]["method"], "GET")
        self.assertIsNone(self.heard.seen[0]["body"])
        self.assertIsNone(self.heard.seen[0]["content_type"])
        self.assertEqual(body, {"event_id": "e1"})

    def test_a_refusal_is_reported_false_and_said_once(self) -> None:
        self.heard.status = 503
        self.heard.body = {"error": "no listener"}
        self.assertFalse(self.client().send("01a1-session", "a message"))
        self.assertFalse(self.client().publish("a.topic", "a notice"))
        self.assertEqual(len(self.said), 2)
        self.assertIn("send to 01a1-session failed: 503", self.said[0])
        self.assertIn("publish to a.topic failed: 503", self.said[1])

    def test_an_error_body_that_is_not_json_is_carried_as_text(self) -> None:
        self.heard.status = 500
        self.heard.body = "<html>bad gateway</html>"
        status, body = self.client().request("POST", "/v1/messages/send", {"a": 1})
        self.assertEqual(status, 500)
        # The listener still answers JSON-encoded, so the string arrives decoded, not wrapped.
        self.assertEqual(body, "<html>bad gateway</html>")

    def test_a_listener_that_is_not_there_answers_zero_and_the_reason(self) -> None:
        os.environ["ENVOY_URL"] = "http://127.0.0.1:1"
        status, body = self.client(timeout=2.0).request("GET", "/v1/sessions")
        self.assertEqual(status, 0)
        self.assertIn("error", body)

    def test_a_caller_that_gives_no_logger_says_nothing_and_still_reports(self) -> None:
        self.heard.status = 503
        self.assertFalse(CLIENT.Envoy().send("01a1-session", "a message"))


if __name__ == "__main__":
    unittest.main()
