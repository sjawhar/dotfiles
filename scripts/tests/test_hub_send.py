#!/usr/bin/env python3
"""Tests for hub-send against a local Matrix hub and bridge-provisioning fake."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock


DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "scripts" / "hub-send"
REAL_YQ = subprocess.run(
    [str(DOTFILES / "bin" / "mise") if (DOTFILES / "bin" / "mise").exists() else "mise", "which", "yq"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
HUB_TOKEN = "hub-token"
HOME_DISCORD_SECRET = "home-discord-secret"
DECOY_DISCORD_SECRET = "decoy-discord-secret"
ROOM = "!room:chathub"
EVENT = "$ev1"


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes
    port: str | None = None


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class InterruptingStringIO(io.StringIO):
    """Deliver real SIGINT and SIGTERM to this process once print has written its newline."""

    def __init__(self) -> None:
        super().__init__()
        self.interrupted = False

    def write(self, text: str) -> int:
        count = super().write(text)
        if text == "\n" and not self.interrupted:
            self.interrupted = True
            os.kill(os.getpid(), signal.SIGINT)
            os.kill(os.getpid(), signal.SIGTERM)
        return count


class FakeHub:
    """A small fake of the Matrix and local bridge APIs hub-send uses."""

    def __init__(self) -> None:
        self.requests: list[Request] = []
        self.timelines: dict[str, list[dict[str, object]]] = {}
        self.states: dict[str, list[dict[str, object]]] = {}
        self.event_ids: dict[str, set[str]] = {}
        self.logins: dict[str, list[dict[str, object]]] = {}
        self.provision_modes: dict[str, str] = {}
        self.post_mode = "ok"
        self.upload_mode = "ok"
        self.state_mode = "ok"
        self.read_failure_modes: list[str] = []
        self.read_failure_forever: str | None = None
        self.relation_page_size = 100
        self.relations_return_every_status = False
        self.relation_reads = 0
        self.relation_read_seconds = 0.0
        self.status_after_reads: int | None = None
        self.after_statuses: list[dict[str, object]] = []
        self.status_due: float | None = None
        self.due_statuses: list[dict[str, object]] = []
        self.post_statuses: list[dict[str, object]] = []
        self.discord_status: dict[str, object] = {"logged_in": True, "connected": True}
        self.clock: FakeClock | None = None
        self.hang_seconds = 0.12
        self._after_added = False
        self._due_added = False
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHubHandler)
        self._server.daemon_threads = True
        self._server.fake = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.01),
            daemon=True,
        )

    @property
    def port(self) -> int:
        return self._server.server_port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def room_state(self, room: str) -> list[dict[str, object]]:
        return self.states.setdefault(
            room,
            [
                {
                    "type": "m.bridge",
                    "content": {
                        "bridgebot": "@gmessagesbot:chathub",
                        "channel": {"fi.mau.receiver": "login-a"},
                    },
                }
            ],
        )

    def set_bridge(
        self,
        room: str,
        bot: str,
        receiver: str | None = "login-a",
    ) -> None:
        content: dict[str, object] = {"bridgebot": bot}
        if receiver is not None:
            content["channel"] = {"fi.mau.receiver": receiver}
        self.states[room] = [{"type": "m.bridge", "content": content}]

    def room_timeline(self, room: str) -> list[dict[str, object]]:
        return self.timelines.setdefault(room, [])

    def room_events(self, room: str) -> set[str]:
        return self.event_ids.setdefault(room, {"$earlier"})

    def logins_for(self, port: str) -> list[dict[str, object]]:
        return self.logins.setdefault(port, [{"id": "login-a", "state": {"state_event": "CONNECTED"}}])

    def add_status(
        self,
        room: str,
        target: str,
        status: str,
        *,
        sender: str = "@gmessagesbot:chathub",
        **fields: object,
    ) -> None:
        content: dict[str, object] = {
            "m.relates_to": {"rel_type": "m.reference", "event_id": target},
            "status": status,
            **fields,
        }
        self.room_timeline(room).append(
            {
                "type": "com.beeper.message_send_status",
                "sender": sender,
                "content": content,
            }
        )

    def add_configured_statuses(self, room: str, statuses: list[dict[str, object]]) -> None:
        for configured in statuses:
            fields = dict(configured)
            target = str(fields.pop("target", EVENT))
            status = str(fields.pop("status"))
            sender = str(fields.pop("sender", "@gmessagesbot:chathub"))
            self.add_status(room, target, status, sender=sender, **fields)

    def post_requests(self) -> list[Request]:
        return [
            request
            for request in self.requests
            if request.method == "PUT" and "/send/m.room.message/" in request.path
        ]

    def upload_requests(self) -> list[Request]:
        return [request for request in self.requests if request.method == "POST" and request.path == "/_matrix/media/v3/upload"]

    def provisioning_requests(self) -> list[Request]:
        return [request for request in self.requests if request.port is not None]


class FakeHubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def fake(self) -> FakeHub:
        return self.server.fake  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        pass

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def _handle(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
        port = parts[1] if len(parts) >= 2 and parts[0] == "port" else None
        request = Request(
            self.command,
            parsed.path,
            urllib.parse.parse_qs(parsed.query, keep_blank_values=True),
            {key.lower(): value for key, value in self.headers.items()},
            body,
            port,
        )
        self.fake.requests.append(request)
        if port is not None:
            self._provision(parts[2:], request)
        else:
            self._hub(parts, request)

    def _authorized(self, token: str) -> bool:
        return self.headers.get("Authorization") == f"Bearer {token}"

    def _json(self, status: int, value: object) -> None:
        data = json.dumps(value, separators=(",", ":")).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _truncated_json(self, status: int) -> None:
        data = b'{"partial":'
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data) + 100))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        self.close_connection = True

    def _error(
        self, status: int, error: str = "request failed", errcode: str = "M_FAKE"
    ) -> None:
        self._json(status, {"errcode": errcode, "error": error})

    def _hang(self) -> None:
        time.sleep(self.fake.hang_seconds)

    def _drop(self) -> None:
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.close()

    def _mode(self, mode: str, *, upload: bool = False) -> bool:
        if mode == "ok" or upload and mode == "no_uri":
            return False
        if mode == "hang":
            self._hang()
            return True
        if mode == "drop":
            self._drop()
            return True
        if mode == "truncated":
            self._truncated_json(200)
            return True
        if mode == "http500":
            self._error(500, "request failed")
            return True
        if mode == "http403":
            self._error(403, "forbidden")
            return True
        if mode == "http403_truncated":
            self._truncated_json(403)
            return True
        if upload and mode == "http413":
            self._error(413, "upload rejected")
            return True
        self._error(500, f"unknown fake mode {mode}")
        return True

    def _hub(self, parts: list[str], request: Request) -> None:
        if not self._authorized(HUB_TOKEN):
            self._error(401, "missing hub token")
            return
        if len(parts) >= 6 and parts[:4] == ["_matrix", "client", "v3", "rooms"] and parts[5] == "state":
            room = parts[4]
            if self._mode(self.fake.state_mode):
                return
            self._json(200, self.fake.room_state(room))
            return
        if (
            len(parts) >= 8
            and parts[:4] == ["_matrix", "client", "v3", "rooms"]
            and parts[5:7] == ["send", "m.room.message"]
            and request.method == "PUT"
        ):
            self._post(parts[4], request)
            return
        if parts == ["_matrix", "media", "v3", "upload"] and request.method == "POST":
            self._upload(request)
            return
        if len(parts) >= 7 and parts[:4] == ["_matrix", "client", "v3", "rooms"] and parts[5] == "event":
            room, event_id = parts[4], parts[6]
            if event_id not in self.fake.room_events(room):
                self._error(404, "event not found")
                return
            self._json(200, {"event_id": event_id})
            return
        if len(parts) >= 6 and parts[:4] == ["_matrix", "client", "v1", "rooms"] and parts[5] == "relations":
            if len(parts) != 9 or parts[7:] != ["m.reference", "com.beeper.message_send_status"]:
                self._error(404, "unrecognized relation endpoint", errcode="M_UNRECOGNIZED")
                return
            self._relations(parts[4], parts[6], request)
            return
        if len(parts) >= 6 and parts[:4] == ["_matrix", "client", "v3", "rooms"] and parts[5] == "messages":
            self._messages(parts[4], request)
            return
        self._error(404, "unknown hub endpoint")

    def _post(self, room: str, request: Request) -> None:
        if self._mode(self.fake.post_mode):
            return
        try:
            content = json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(400, "bad JSON")
            return
        self.fake.room_events(room).add(EVENT)
        self.fake.room_timeline(room).append({"type": "m.room.message", "event_id": EVENT, "content": content})
        self.fake.add_configured_statuses(room, self.fake.post_statuses)
        self._json(200, {"event_id": EVENT})

    def _upload(self, _request: Request) -> None:
        if self._mode(self.fake.upload_mode, upload=True):
            return
        if self.fake.upload_mode == "no_uri":
            self._json(200, {})
            return
        self._json(200, {"content_uri": "mxc://chathub/uploaded"})

    def _take_read_mode(self) -> str | None:
        if self.fake.read_failure_modes:
            return self.fake.read_failure_modes.pop(0)
        return self.fake.read_failure_forever

    def _relations(self, room: str, event_id: str, request: Request) -> None:
        if request.query.get("dir") != ["f"] or request.query.get("limit") != ["100"]:
            self._error(400, "relations must be forward and limited")
            return
        self.fake.relation_reads += 1
        if self.fake.clock is not None:
            self.fake.clock.sleep(self.fake.relation_read_seconds)
        if self.fake.status_after_reads is not None and not self.fake._after_added and self.fake.relation_reads > self.fake.status_after_reads:
            self.fake.add_configured_statuses(room, self.fake.after_statuses)
            self.fake._after_added = True
        if (
            self.fake.status_due is not None
            and not self.fake._due_added
            and self.fake.clock is not None
            and self.fake.clock() >= self.fake.status_due
        ):
            self.fake.add_configured_statuses(room, self.fake.due_statuses)
            self.fake._due_added = True
        read_mode = self._take_read_mode()
        if read_mode is not None and self._mode(read_mode):
            return
        statuses = [
            event
            for event in self.fake.room_timeline(room)
            if event.get("type") == "com.beeper.message_send_status"
            and (
                self.fake.relations_return_every_status
                or isinstance(event.get("content"), dict)
                and event["content"].get("m.relates_to", {}).get("event_id") == event_id
            )
        ]
        start = int(request.query.get("from", ["0"])[0])
        end = start + self.fake.relation_page_size
        response: dict[str, object] = {"chunk": statuses[start:end]}
        if end < len(statuses):
            response["next_batch"] = str(end)
        self._json(200, response)

    def _messages(self, room: str, request: Request) -> None:
        if request.query.get("dir") != ["b"]:
            self._error(400, "messages must be backwards")
            return
        self._json(200, {"chunk": list(reversed(self.fake.room_timeline(room)))})

    def _provision(self, parts: list[str], request: Request) -> None:
        port = request.port
        assert port is not None
        if parts == ["_matrix", "provision", "v3", "whoami"]:
            if not self._authorized(HUB_TOKEN) or request.query.get("user_id") != ["@sami:chathub"]:
                self._error(401, "missing Matrix authentication")
                return
            if self._mode(self.fake.provision_modes.get(port, "ok")):
                return
            self._json(200, {"logins": self.fake.logins_for(port)})
            return
        if parts == ["_matrix", "provision", "v1", "ping"]:
            if not self._authorized(HOME_DISCORD_SECRET) or request.query.get("user_id") != ["@sami:chathub"]:
                self._error(401, "missing Discord secret")
                return
            if self._mode(self.fake.provision_modes.get(port, "ok")):
                return
            self._json(200, {"Discord": self.fake.discord_status})
            return
        self._error(404, "unknown provisioning endpoint")


class HubSendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.xdg = self.root / "xdg"
        self.bin = self.root / "bin"
        self.home.mkdir()
        self.xdg.mkdir()
        self.bin.mkdir()
        (self.bin / "yq").symlink_to(REAL_YQ)
        self.write_discord_secret(self.home, HOME_DISCORD_SECRET)
        self.write_discord_secret(self.xdg, DECOY_DISCORD_SECRET)
        self.start_fake()

    def tearDown(self) -> None:
        self.fake.close()
        self.temporary.cleanup()

    def start_fake(self) -> None:
        self.fake = FakeHub()
        self.fake.start()

    def restart_fake(self) -> None:
        self.fake.close()
        self.start_fake()

    def write_discord_secret(self, root: Path, secret: str) -> None:
        config = root / ".local" / "share" / "chathub" / "discord" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            f"bridge:\n  provisioning:\n    shared_secret: {json.dumps(secret)}\n",
            encoding="utf-8",
        )

    def load_module(self):
        name = f"hub_send_test_{id(self)}_{time.time_ns()}"
        loader = importlib.machinery.SourceFileLoader(name, str(SCRIPT))
        spec = importlib.util.spec_from_loader(name, loader)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        loader.exec_module(module)
        module.PROVISION_URL = f"{self.fake.url}/port/{{port}}"
        module.POST_TIMEOUT = 0.03
        module.READ_TIMEOUT = 0.03
        return name, module

    def invoke(
        self,
        args: list[str],
        *,
        interrupt: str | None = None,
        interrupt_during_provision: bool = False,
        interrupt_during_emit: bool = False,
        env: dict[str, str] | None = None,
        configure_module=None,
        urlopen_errors: list[BaseException] | None = None,
    ) -> tuple[int, str, str, FakeClock]:
        name, module = self.load_module()
        if configure_module is not None:
            configure_module(module)
        clock = FakeClock()
        self.fake.clock = clock
        old_urlopen = module.urllib.request.urlopen

        def sleeper(seconds: float) -> None:
            if interrupt == "keyboard":
                raise KeyboardInterrupt()
            if interrupt == "module":
                raise module.Interrupted()
            if interrupt == "sigterm":
                os.kill(os.getpid(), signal.SIGTERM)
            clock.sleep(seconds)

        def guarded_urlopen(request, *request_args, **request_kwargs):
            url = request.full_url if hasattr(request, "full_url") else str(request)
            parsed = urllib.parse.urlsplit(url)
            if parsed.hostname != "127.0.0.1" or parsed.port != self.fake.port:
                self.fail(f"hub-send requested outside the fake: {url}")
            if interrupt_during_provision and parsed.path.startswith("/port/"):
                sleeper(0)
            try:
                return old_urlopen(request, *request_args, **request_kwargs)
            except (socket.timeout, TimeoutError) as error:
                if urlopen_errors is not None:
                    urlopen_errors.append(error)
                raise
            except urllib.error.URLError as error:
                if urlopen_errors is not None and isinstance(
                    error.reason, (socket.timeout, TimeoutError)
                ):
                    urlopen_errors.append(error.reason)
                raise

        variables = {
            "HOME": str(self.home),
            "XDG_DATA_HOME": str(self.xdg),
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "MATRIX_HUB_URL": self.fake.url,
            "MATRIX_HUB_TOKEN": HUB_TOKEN,
        }
        if env is not None:
            variables.update(env)
        stdout = InterruptingStringIO() if interrupt_during_emit else io.StringIO()
        stderr = io.StringIO()
        try:
            with (
                mock.patch.dict(os.environ, variables, clear=False),
                mock.patch.object(module.urllib.request, "urlopen", guarded_urlopen),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                try:
                    result = module.main(args, clock=clock, sleep=sleeper)
                except SystemExit as error:
                    result = int(error.code)
        finally:
            sys.modules.pop(name, None)
        self.assertLessEqual(len(self.fake.post_requests()), 1)
        return result, stdout.getvalue(), stderr.getvalue(), clock

    def text_file(self, content: str, name: str = "text.txt") -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def binary_file(self, content: bytes, name: str) -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path

    def text_args(self, text: str = "hello", room: str = ROOM, *extra: str) -> list[str]:
        return [*extra, room, "--text-file", str(self.text_file(text))]

    def forced_text_args(self, text: str = "hello", room: str = ROOM) -> list[str]:
        return ["--force", room, "--text-file", str(self.text_file(text))]

    def configure_success(self) -> None:
        self.fake.post_statuses = [{"status": "SUCCESS"}]

    def assert_result(self, actual: tuple[int, str, str, FakeClock], line: str, code: int) -> FakeClock:
        result, stdout, stderr, clock = actual
        self.assertEqual(result, code)
        self.assertEqual(stdout, line + "\n")
        self.assertEqual(stderr, "")
        return clock

    def assert_usage(self, actual: tuple[int, str, str, FakeClock]) -> None:
        result, stdout, stderr, _clock = actual
        self.assertEqual(result, 2)
        self.assertEqual(stdout, "")
        self.assertIn("usage: hub-send", stderr)
        self.assertEqual(self.fake.post_requests(), [])

    def assert_no_provisioning(self) -> None:
        self.assertEqual(self.fake.provisioning_requests(), [])

    def message_content(self) -> dict[str, object]:
        posts = self.fake.post_requests()
        self.assertEqual(len(posts), 1)
        return json.loads(posts[0].body.decode("utf-8"))

    def test_bridge_table_reads_all_entries_from_the_real_installer(self) -> None:
        name, module = self.load_module()
        try:
            bridges = module.bridge_table()
        finally:
            sys.modules.pop(name, None)
        self.assertEqual(
            [(bridge.instance, bridge.port, bridge.bot, bridge.layout) for bridge in bridges.values()],
            [
                ("gmessages", "29336", "@gmessagesbot:chathub", "bridgev2"),
                ("whatsapp", "29318", "@whatsappbot:chathub", "bridgev2"),
                ("signal", "29328", "@signalbot:chathub", "bridgev2"),
                ("meta", "29319", "@metabot:chathub", "bridgev2"),
                ("instagram", "29320", "@instagrambot:chathub", "bridgev2"),
                ("discord", "29334", "@discordbot:chathub", "legacy"),
                ("linkedin", "29341", "@linkedinbot:chathub", "bridgev2"),
            ],
        )

    # (a) through (s), as listed in verification.md.
    def test_a_success_for_the_posted_event(self) -> None:
        self.configure_success()
        self.assert_result(self.invoke(self.text_args()), f"SENT {EVENT}", 0)

    def test_b_fail_permanent_with_reason(self) -> None:
        self.fake.post_statuses = [{"status": "FAIL_PERMANENT", "message": "permanent failure", "reason": "m.permanent"}]
        self.assert_result(self.invoke(self.text_args()), f"FAILED {EVENT}: permanent failure [m.permanent]", 4)

    def test_c_fail_retriable_with_reason(self) -> None:
        self.fake.post_statuses = [{"status": "FAIL_RETRIABLE", "error": "retry later", "reason": "m.retry"}]
        self.assert_result(self.invoke(self.text_args()), f"FAILED {EVENT}: retry later [m.retry]", 4)

    def test_d_pending_until_deadline(self) -> None:
        self.fake.post_statuses = [{"status": "PENDING"}]
        clock = self.assert_result(self.invoke(self.text_args()), f"UNCONFIRMED {EVENT}", 5)
        self.assertEqual(clock(), 60)

    def test_e_success_for_a_different_event_is_ignored(self) -> None:
        for relations_return_every_status in (False, True):
            with self.subTest(relations_return_every_status=relations_return_every_status):
                self.fake.relations_return_every_status = relations_return_every_status
                self.fake.add_status(ROOM, "$other", "SUCCESS")
                clock = self.assert_result(self.invoke(self.text_args()), f"UNCONFIRMED {EVENT}", 5)
                self.assertEqual(clock(), 60)
            if not relations_return_every_status:
                self.restart_fake()

    def test_f_status_before_the_post_response_is_found(self) -> None:
        self.configure_success()
        self.assert_result(self.invoke(self.text_args()), f"SENT {EVENT}", 0)
        self.assertGreaterEqual(self.fake.relation_reads, 1)

    def test_g_post_http_error(self) -> None:
        self.fake.post_mode = "http500"
        self.assert_result(self.invoke(self.text_args()), "FAILED -: M_FAKE: request failed", 4)

    def test_h_post_timeout(self) -> None:
        self.fake.post_mode = "hang"
        self.assert_result(self.invoke(self.text_args()), "UNCONFIRMED -", 5)

    def test_post_connection_drop_is_unconfirmed(self) -> None:
        self.fake.post_mode = "drop"
        self.assert_result(self.invoke(self.text_args()), "UNCONFIRMED -", 5)

    def test_i_unknown_login_refuses_and_force_posts(self) -> None:
        self.fake.logins["29336"] = [{"id": "login-a", "state": {"state_event": "UNKNOWN_ERROR", "info": {"error": "offline"}}}]
        self.assert_result(
            self.invoke(self.text_args()),
            "REFUSED: bridge gmessages login login-a is UNKNOWN_ERROR: offline",
            3,
        )
        self.assertEqual(self.fake.post_requests(), [])
        self.restart_fake()
        self.configure_success()
        self.assert_result(self.invoke(self.forced_text_args()), f"SENT {EVENT}", 0)
        self.assert_no_provisioning()

    def test_j_selected_login_must_be_connected(self) -> None:
        self.fake.logins["29336"] = [
            {"id": "login-a", "state": {"state_event": "UNKNOWN_ERROR"}},
            {"id": "login-b", "state": {"state_event": "CONNECTED"}},
        ]
        self.assert_result(self.invoke(self.text_args()), "REFUSED: bridge gmessages login login-a is UNKNOWN_ERROR", 3)
        self.assertEqual(self.fake.provisioning_requests()[0].port, "29336")

    def test_j_other_login_does_not_block_selected_connected_login(self) -> None:
        self.fake.logins["29336"] = [
            {"id": "login-a", "state": {"state_event": "CONNECTED"}},
            {"id": "login-b", "state": {"state_event": "UNKNOWN_ERROR"}},
        ]
        self.configure_success()
        self.assert_result(self.invoke(self.text_args()), f"SENT {EVENT}", 0)
        self.assertEqual(self.fake.provisioning_requests()[0].port, "29336")

    def test_k_no_m_bridge_refuses_even_with_force(self) -> None:
        self.fake.states[ROOM] = []
        self.assert_result(self.invoke(self.text_args()), "REFUSED: room has no m.bridge state", 3)
        self.assertEqual(self.fake.post_requests(), [])
        self.restart_fake()
        self.fake.states[ROOM] = []
        self.assert_result(self.invoke(self.forced_text_args()), "REFUSED: room has no m.bridge state", 3)
        self.assertEqual(self.fake.post_requests(), [])
        self.assert_no_provisioning()

    def test_l_provisioning_timeout_refuses_and_force_posts(self) -> None:
        self.fake.provision_modes["29336"] = "hang"
        self.assert_result(
            self.invoke(self.text_args()),
            "REFUSED: bridge gmessages provisioning check failed: timed out",
            3,
        )
        self.assertEqual(self.fake.provisioning_requests()[0].port, "29336")
        self.restart_fake()
        self.configure_success()
        self.assert_result(self.invoke(self.forced_text_args()), f"SENT {EVENT}", 0)
        self.assert_no_provisioning()

    def test_m_discord_disconnected_refuses(self) -> None:
        self.fake.set_bridge(ROOM, "@discordbot:chathub")
        self.fake.discord_status = {"logged_in": True, "connected": False}
        self.assert_result(self.invoke(self.text_args()), "REFUSED: bridge discord login @sami:chathub is DISCONNECTED", 3)
        requests = self.fake.provisioning_requests()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].port, "29334")

    def test_n_png_upload_and_caption(self) -> None:
        png = self.binary_file(b"\x89PNG\r\n\x1a\n", "picture.png")
        caption = self.text_file("a picture", "caption.txt")
        self.configure_success()
        self.assert_result(self.invoke([ROOM, "--file", str(png), "--caption-file", str(caption)]), f"SENT {EVENT}", 0)
        uploads = self.fake.upload_requests()
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0].query, {"filename": ["picture.png"]})
        self.assertEqual(uploads[0].headers["content-type"], "image/png")
        self.assertEqual(
            self.message_content(),
            {
                "msgtype": "m.image",
                "body": "a picture",
                "filename": "picture.png",
                "url": "mxc://chathub/uploaded",
                "info": {"mimetype": "image/png", "size": 8},
            },
        )

    def test_n_pdf_upload_without_caption(self) -> None:
        pdf = self.binary_file(b"%PDF-1.7", "report.pdf")
        self.configure_success()
        self.assert_result(self.invoke([ROOM, "--file", str(pdf)]), f"SENT {EVENT}", 0)
        self.assertEqual(len(self.fake.upload_requests()), 1)
        self.assertEqual(
            self.message_content(),
            {
                "msgtype": "m.file",
                "body": "report.pdf",
                "filename": "report.pdf",
                "url": "mxc://chathub/uploaded",
                "info": {"mimetype": "application/pdf", "size": 8},
            },
        )

    def test_o_bad_arguments(self) -> None:
        text = self.text_file("text")
        file = self.binary_file(b"file", "file.bin")
        caption = self.text_file("caption", "caption.txt")
        self.assert_usage(self.invoke(["room", "--text-file", str(text)]))
        self.assert_usage(self.invoke([ROOM, "--reply-to", "event", "--text-file", str(text)]))
        self.assert_usage(self.invoke([ROOM, "--caption-file", str(caption), "--text-file", str(text)]))
        self.assert_usage(self.invoke([ROOM, "--text-file", str(text), "--file", str(file)]))
        self.assert_usage(self.invoke([ROOM]))

    def test_p_default_wait_is_sixty_seconds(self) -> None:
        self.fake.status_due = 60
        self.fake.due_statuses = [{"status": "SUCCESS"}]
        clock = self.assert_result(self.invoke(self.text_args()), f"SENT {EVENT}", 0)
        self.assertEqual(clock(), 60)

    def test_q_reply_to_existing_event(self) -> None:
        self.configure_success()
        self.assert_result(self.invoke(self.text_args("reply", ROOM, "--reply-to", "$earlier")), f"SENT {EVENT}", 0)
        self.assertEqual(
            self.message_content()["m.relates_to"],
            {"m.in_reply_to": {"event_id": "$earlier"}},
        )

    def test_r_all_logins_must_be_connected_without_receiver(self) -> None:
        self.fake.set_bridge(ROOM, "@gmessagesbot:chathub", None)
        self.fake.logins["29336"] = [
            {"id": "login-a", "state": {"state_event": "CONNECTED"}},
            {"id": "login-b", "state": {"state_event": "UNKNOWN_ERROR"}},
        ]
        self.assert_result(self.invoke(self.text_args()), "REFUSED: bridge gmessages login login-b is UNKNOWN_ERROR", 3)
        self.assertEqual(self.fake.provisioning_requests()[0].port, "29336")
        self.restart_fake()
        self.fake.set_bridge(ROOM, "@gmessagesbot:chathub", None)
        self.configure_success()
        self.assert_result(self.invoke(self.forced_text_args()), f"SENT {EVENT}", 0)
        self.assert_no_provisioning()

    def test_s_every_installed_bridge_uses_its_own_port(self) -> None:
        name, module = self.load_module()
        try:
            bridges = list(module.bridge_table().values())
        finally:
            sys.modules.pop(name, None)
        self.assertEqual(len(bridges), 7)
        for index, bridge in enumerate(bridges):
            instance, port, bot = bridge.instance, bridge.port, bridge.bot
            self.assertIsNotNone(instance)
            self.assertIsNotNone(port)
            with self.subTest(instance=instance, force=False):
                self.fake.set_bridge(ROOM, bot)
                self.fake.provision_modes[port] = "hang"
                result, stdout, stderr, _clock = self.invoke(self.text_args())
                self.assertEqual(result, 3)
                self.assertTrue(stdout.startswith(f"REFUSED: bridge {instance} provisioning check failed: "))
                self.assertEqual(len(stdout.splitlines()), 1)
                self.assertEqual(stderr, "")
                result = self.fake.provisioning_requests()
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0].port, port)
                self.assertEqual(self.fake.post_requests(), [])
            self.restart_fake()
            with self.subTest(instance=instance, force=True):
                self.fake.set_bridge(ROOM, bot)
                self.configure_success()
                self.assert_result(self.invoke(self.forced_text_args()), f"SENT {EVENT}", 0)
                self.assert_no_provisioning()
            if index != len(bridges) - 1:
                self.restart_fake()

    # Additional decision cases from plan.md lines 73-97.
    def test_pending_then_fail_permanent_uses_first_terminal_status(self) -> None:
        self.fake.post_statuses = [{"status": "PENDING"}, {"status": "FAIL_PERMANENT", "reason": "m.permanent"}]
        self.assert_result(self.invoke(self.text_args()), f"FAILED {EVENT}: m.permanent", 4)

    def test_two_success_statuses_send_once(self) -> None:
        self.fake.post_statuses = [{"status": "SUCCESS"}, {"status": "SUCCESS"}]
        self.assert_result(self.invoke(self.text_args()), f"SENT {EVENT}", 0)

    def test_retriable_before_success_reports_failed(self) -> None:
        self.fake.post_statuses = [{"status": "FAIL_RETRIABLE", "reason": "m.retry"}, {"status": "SUCCESS"}]
        self.assert_result(self.invoke(self.text_args()), f"FAILED {EVENT}: m.retry", 4)

    def test_status_on_second_relations_page_is_found(self) -> None:
        self.fake.relation_page_size = 1
        self.fake.post_statuses = [{"status": "PENDING"}, {"status": "SUCCESS"}]
        self.assert_result(self.invoke(self.text_args()), f"SENT {EVENT}", 0)
        self.assertEqual(self.fake.relation_reads, 2)

    def test_discord_ghost_status_is_accepted(self) -> None:
        self.fake.set_bridge(ROOM, "@discordbot:chathub")
        self.fake.post_statuses = [{"status": "SUCCESS", "sender": "@discord_123:chathub"}]
        self.assert_result(self.invoke(self.text_args()), f"SENT {EVENT}", 0)

    def test_reason_fields_and_newlines_are_collapsed(self) -> None:
        cases = [
            ({"message": "message", "reason": "m.code"}, "message [m.code]"),
            ({"error": "error", "reason": "m.code"}, "error [m.code]"),
            ({"internal_error": "internal", "reason": "m.code"}, "internal [m.code]"),
            ({"reason": "m.code"}, "m.code"),
            ({"message": "line one\n\nline two", "reason": "m.code"}, "line one line two [m.code]"),
        ]
        for fields, reason in cases:
            with self.subTest(fields=fields):
                self.fake.post_statuses = [{"status": "FAIL_PERMANENT", **fields}]
                self.assert_result(self.invoke(self.text_args()), f"FAILED {EVENT}: {reason}", 4)
            self.restart_fake()

    def test_status_read_failures_then_success(self) -> None:
        self.fake.read_failure_modes = ["http500", "hang"]
        self.fake.hang_seconds = 1.2
        self.fake.status_after_reads = 2
        self.fake.after_statuses = [{"status": "SUCCESS"}]
        timeout_errors: list[BaseException] = []
        self.assert_result(
            self.invoke(self.text_args(), urlopen_errors=timeout_errors),
            f"SENT {EVENT}",
            0,
        )
        self.assertEqual(self.fake.relation_reads, 3)
        self.assertTrue(
            any(isinstance(error, (socket.timeout, TimeoutError)) for error in timeout_errors)
        )

    def test_truncated_status_read_retries_then_sends(self) -> None:
        self.configure_success()
        self.fake.read_failure_modes = ["truncated"]
        self.assert_result(self.invoke(self.text_args()), f"SENT {EVENT}", 0)
        self.assertEqual(self.fake.relation_reads, 2)

    def test_truncated_post_error_keeps_http_status(self) -> None:
        self.fake.post_mode = "http403_truncated"
        self.assert_result(self.invoke(self.text_args()), "FAILED -: HTTP 403", 4)

    def test_truncated_upload_does_not_post_a_message(self) -> None:
        self.fake.upload_mode = "truncated"
        file = self.binary_file(b"data", "truncated.bin")
        self.assert_result(
            self.invoke([ROOM, "--file", str(file)]),
            "FAILED -: upload: HTTP response read failed",
            4,
        )
        self.assertEqual(self.fake.post_requests(), [])

    def test_truncated_state_read_refuses(self) -> None:
        self.fake.state_mode = "truncated"
        self.assert_result(
            self.invoke(self.forced_text_args()),
            "REFUSED: cannot read the room's state: HTTP response read failed",
            3,
        )
        self.assertEqual(self.fake.post_requests(), [])
        self.assert_no_provisioning()

    def test_truncated_provisioning_read_refuses(self) -> None:
        self.fake.provision_modes["29336"] = "truncated"
        self.assert_result(
            self.invoke(self.text_args()),
            "REFUSED: bridge gmessages provisioning check failed: HTTP response read failed",
            3,
        )
        self.assertEqual(self.fake.post_requests(), [])

    def test_status_pagination_stops_at_the_deadline(self) -> None:
        self.fake.relation_page_size = 1
        self.fake.relation_read_seconds = 9
        self.fake.post_statuses = [{"status": "PENDING"} for _ in range(9)] + [
            {"status": "SUCCESS"}
        ]
        clock = self.assert_result(self.invoke(self.text_args()), f"UNCONFIRMED {EVENT}", 5)
        self.assertLessEqual(clock(), 70)
        self.assertLess(self.fake.relation_reads, 10)

    def test_status_pagination_succeeds_within_the_deadline(self) -> None:
        self.fake.relation_page_size = 1
        self.fake.relation_read_seconds = 9
        self.fake.post_statuses = [{"status": "PENDING"}, {"status": "SUCCESS"}]
        clock = self.assert_result(self.invoke(self.text_args()), f"SENT {EVENT}", 0)
        self.assertEqual(clock(), 18)

    def test_status_read_failures_until_deadline(self) -> None:
        self.fake.read_failure_forever = "http500"
        clock = self.assert_result(self.invoke(self.text_args()), f"UNCONFIRMED {EVENT}", 5)
        self.assertEqual(clock(), 60)

    def test_keyboard_interrupt_during_wait_is_unconfirmed(self) -> None:
        self.assert_result(self.invoke(self.text_args(), interrupt="keyboard"), f"UNCONFIRMED {EVENT}", 5)

    def test_module_interrupted_during_wait_is_unconfirmed(self) -> None:
        self.assert_result(self.invoke(self.text_args(), interrupt="module"), f"UNCONFIRMED {EVENT}", 5)

    def test_keyboard_interrupt_during_bridge_check_refuses_without_post(self) -> None:
        self.assert_result(
            self.invoke(self.text_args(), interrupt="keyboard", interrupt_during_provision=True),
            "REFUSED: interrupted",
            3,
        )
        self.assertEqual(self.fake.post_requests(), [])


    def test_keyboard_interrupt_before_post_request_refuses_without_put(self) -> None:
        def interrupt_transaction_id(module) -> None:
            module.transaction_id = mock.Mock(side_effect=KeyboardInterrupt)

        self.assert_result(
            self.invoke(
                self.forced_text_args(),
                configure_module=interrupt_transaction_id,
            ),
            "REFUSED: interrupted",
            3,
        )
        self.assertEqual([request for request in self.fake.requests if request.method == "PUT"], [])

    def test_keyboard_interrupt_during_text_read_refuses_without_request(self) -> None:
        def interrupt_read_text_file(_path, _label) -> None:
            raise KeyboardInterrupt()

        self.assert_result(
            self.invoke(
                self.text_args(),
                configure_module=lambda module: setattr(module, "read_text_file", interrupt_read_text_file),
            ),
            "REFUSED: interrupted",
            3,
        )
        self.assertEqual(self.fake.requests, [])

    def test_interrupt_handlers_are_restored_after_usage_error(self) -> None:
        sigint = signal.getsignal(signal.SIGINT)
        sigterm = signal.getsignal(signal.SIGTERM)
        self.assert_usage(self.invoke(["not-a-room", "--text-file", str(self.text_file("text"))]))
        self.assertEqual(signal.getsignal(signal.SIGINT), sigint)
        self.assertEqual(signal.getsignal(signal.SIGTERM), sigterm)

    def test_interrupt_during_result_emit_keeps_one_result_line(self) -> None:
        self.configure_success()
        self.assert_result(
            self.invoke(self.text_args(), interrupt_during_emit=True),
            f"SENT {EVENT}",
            0,
        )

    def test_upload_failures_do_not_post_a_message(self) -> None:
        for mode, expected in [
            ("http413", "FAILED -: upload: M_FAKE: upload rejected"),
            ("hang", "FAILED -: upload: timed out"),
            ("no_uri", "FAILED -: upload: response has no content_uri"),
        ]:
            with self.subTest(mode=mode):
                self.fake.upload_mode = mode
                file = self.binary_file(b"data", f"{mode}.bin")
                self.assert_result(self.invoke([ROOM, "--file", str(file)]), expected, 4)
                self.assertEqual(self.fake.post_requests(), [])
            self.restart_fake()

    def test_upload_connection_drop_does_not_post_a_message(self) -> None:
        self.fake.upload_mode = "drop"
        result, stdout, stderr, _clock = self.invoke([ROOM, "--file", str(self.binary_file(b"data", "drop.bin"))])
        self.assertEqual(result, 4)
        self.assertTrue(stdout.startswith("FAILED -: upload: "))
        self.assertEqual(len(stdout.splitlines()), 1)
        self.assertEqual(stderr, "")
        self.assertEqual(self.fake.post_requests(), [])

    def test_text_trailing_newlines_are_stripped_but_other_space_is_kept(self) -> None:
        text = "  first line\n second line  \n\n"
        self.configure_success()
        self.assert_result(self.invoke(self.text_args(text)), f"SENT {EVENT}", 0)
        self.assertEqual(self.message_content()["body"], "  first line\n second line  ")

    def test_empty_missing_whitespace_and_non_utf8_text_are_usage_errors(self) -> None:
        empty = self.text_file("", "empty.txt")
        whitespace = self.text_file(" \t\n\r\n", "whitespace.txt")
        invalid = self.binary_file(b"\xff", "invalid.txt")
        missing = self.root / "missing.txt"
        for path in (empty, whitespace, invalid, missing):
            with self.subTest(path=path.name):
                self.assert_usage(self.invoke([ROOM, "--text-file", str(path)]))

    def test_extensionless_file_is_a_generic_file(self) -> None:
        file = self.binary_file(b"unknown", "attachment")
        self.configure_success()
        self.assert_result(self.invoke([ROOM, "--file", str(file)]), f"SENT {EVENT}", 0)
        self.assertEqual(self.message_content()["msgtype"], "m.file")
        self.assertEqual(self.message_content()["info"], {"mimetype": "application/octet-stream", "size": 7})

    def test_reply_target_not_in_room_is_a_usage_error(self) -> None:
        self.assert_usage(self.invoke(self.text_args("hello", ROOM, "--reply-to", "$missing")))

    def test_state_down_or_forbidden_refuses_with_and_without_force(self) -> None:
        for mode in ("drop", "http403"):
            for force in (False, True):
                with self.subTest(mode=mode, force=force):
                    self.fake.state_mode = mode
                    args = self.forced_text_args() if force else self.text_args()
                    result, stdout, stderr, _clock = self.invoke(args)
                    self.assertEqual(result, 3)
                    self.assertTrue(stdout.startswith("REFUSED: cannot read the room's state: "))
                    self.assertEqual(len(stdout.splitlines()), 1)
                    self.assertEqual(stderr, "")
                    self.assertEqual(self.fake.post_requests(), [])
                    if force:
                        self.assert_no_provisioning()
                self.restart_fake()

    def test_unknown_bridgebot_refuses_but_force_posts(self) -> None:
        self.fake.set_bridge(ROOM, "@unknownbot:chathub")
        self.assert_result(
            self.invoke(self.text_args()),
            "REFUSED: room bridge bot @unknownbot:chathub is not in the bridge table",
            3,
        )
        self.assertEqual(self.fake.post_requests(), [])
        self.restart_fake()
        self.fake.set_bridge(ROOM, "@unknownbot:chathub")
        self.configure_success()
        self.assert_result(self.invoke(self.forced_text_args()), f"SENT {EVENT}", 0)
        self.assert_no_provisioning()

    def test_receiver_not_among_logins_refuses(self) -> None:
        self.fake.set_bridge(ROOM, "@gmessagesbot:chathub", "missing-login")
        self.assert_result(self.invoke(self.text_args()), "REFUSED: bridge gmessages has no login missing-login", 3)
        self.assertEqual(self.fake.post_requests(), [])

    def test_no_login_and_no_receiver_refuses(self) -> None:
        self.fake.set_bridge(ROOM, "@gmessagesbot:chathub", None)
        self.fake.logins["29336"] = []
        self.assert_result(self.invoke(self.text_args()), "REFUSED: bridge gmessages has no login", 3)
        self.assertEqual(self.fake.post_requests(), [])

    def test_discord_uses_home_secret_not_xdg_data_home(self) -> None:
        self.fake.set_bridge(ROOM, "@discordbot:chathub")
        self.configure_success()
        self.assert_result(self.invoke(self.text_args()), f"SENT {EVENT}", 0)
        ping = self.fake.provisioning_requests()[0]
        self.assertEqual(ping.port, "29334")
        self.assertEqual(ping.headers["authorization"], f"Bearer {HOME_DISCORD_SECRET}")
        self.assertNotEqual(ping.headers["authorization"], f"Bearer {DECOY_DISCORD_SECRET}")

    def test_control_character_hub_token_is_a_usage_error_without_requests(self) -> None:
        token = "synthetic-token\nsecond-part"
        result, stdout, stderr, _clock = self.invoke(
            self.text_args(),
            env={"MATRIX_HUB_TOKEN": token},
        )
        self.assertEqual(result, 2)
        self.assertEqual(stdout, "")
        self.assertIn("MATRIX_HUB_TOKEN", stderr)
        for fragment in ("synthetic-token", "second-part"):
            self.assertNotIn(fragment, stdout)
            self.assertNotIn(fragment, stderr)
        self.assertEqual(self.fake.requests, [])

    def test_control_character_discord_secret_refuses_and_force_skips_it(self) -> None:
        secret = "discord-first\nsecond-part"
        self.write_discord_secret(self.home, secret)
        self.fake.set_bridge(ROOM, "@discordbot:chathub")
        actual = self.invoke(self.text_args())
        self.assert_result(
            actual,
            "REFUSED: cannot read Discord shared secret",
            3,
        )
        _result, stdout, stderr, _clock = actual
        self.assert_no_provisioning()
        headers = "\n".join(
            value for request in self.fake.requests for value in request.headers.values()
        )
        for fragment in ("discord-first", "second-part"):
            self.assertNotIn(fragment, stdout)
            self.assertNotIn(fragment, stderr)
            self.assertNotIn(fragment, headers)
        self.configure_success()
        self.assert_result(self.invoke(self.forced_text_args()), f"SENT {EVENT}", 0)
        self.assert_no_provisioning()

    def test_check_room_is_validation_only_and_bridge_fields_are_required(self) -> None:
        name, module = self.load_module()
        try:
            self.assertIsNone(module.check_room(module.Hub(self.fake.url, HUB_TOKEN), ROOM, True))
            with self.assertRaises(TypeError):
                module.BridgeInfo(bot="@gmessagesbot:chathub")
        finally:
            sys.modules.pop(name, None)
        self.assert_no_provisioning()

    def test_missing_home_discord_config_refuses_but_force_posts(self) -> None:
        shutil.rmtree(self.home / ".local")
        self.fake.set_bridge(ROOM, "@discordbot:chathub")
        self.assert_result(self.invoke(self.text_args()), "REFUSED: cannot read Discord shared secret", 3)
        self.assertEqual(self.fake.post_requests(), [])
        self.restart_fake()
        self.fake.set_bridge(ROOM, "@discordbot:chathub")
        self.configure_success()
        self.assert_result(self.invoke(self.forced_text_args()), f"SENT {EVENT}", 0)
        self.assert_no_provisioning()

    def test_real_sigterm_during_wait_is_unconfirmed(self) -> None:
        self.assert_result(self.invoke(self.text_args(), interrupt="sigterm"), f"UNCONFIRMED {EVENT}", 5)

    def test_executable_subprocess_and_bad_arguments(self) -> None:
        text = self.text_file("subprocess")
        self.configure_success()
        environment = {
            **os.environ,
            "HOME": str(self.home),
            "MATRIX_HUB_URL": self.fake.url,
            "MATRIX_HUB_TOKEN": HUB_TOKEN,
        }
        sent = subprocess.run(
            [str(SCRIPT), "--force", ROOM, "--text-file", str(text)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=5,
        )
        self.assertEqual(sent.returncode, 0)
        self.assertEqual(sent.stdout, f"SENT {EVENT}\n")
        self.assertEqual(sent.stderr, "")
        self.assertLessEqual(len(self.fake.post_requests()), 1)
        bad = subprocess.run(
            [str(SCRIPT), "not-a-room", "--text-file", str(text)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=5,
        )
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(bad.stdout, "")
        self.assertIn("usage: hub-send", bad.stderr)


if __name__ == "__main__":
    unittest.main()
