"""Live updates: the server pushes the page's version over a WebSocket, and a new version follows a change of the
collected snapshot or of a file the page reads per request, never a refresh that found nothing new. A restart serves
the last saved snapshot at once."""

from __future__ import annotations

import base64
import copy
import json
import os
import socket
import struct
import threading

import pytest
from test_dashboard import snapshot

from chief_dashboard import server


@pytest.fixture
def dash(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "WATCH_SECONDS", 0.2)
    d = server.Dashboard()
    d.set_snapshot(snapshot())
    return d


def test_version_moves_with_content_not_with_read_times_or_heartbeats(dash, tmp_path):
    v = dash.version()
    same = copy.deepcopy(dash.snapshot)
    same["read_at"] = "2026-10-24T12:02:00Z"
    same["prs"] = {"read_at": "2026-10-24T12:02:00Z", "states": {}}
    dash.set_snapshot(same)
    v_prs = dash.version()  # an empty pull-request read is content: it may change the page
    beat = copy.deepcopy(dash.snapshot)
    beat["read_at"] = "2026-10-24T12:04:00Z"
    beat["prs"]["read_at"] = "2026-10-24T12:04:00Z"
    beat["agents"]["s-1"]["last_activity"] = "2026-10-24T12:03:59Z"
    dash.set_snapshot(beat)
    assert dash.version() == v_prs
    moved = copy.deepcopy(dash.snapshot)
    moved["scoreboards"]["BET-83"]["versions"][-1]["data"]["numbers"][0]["values"].append(
        {"at": "2026-10-24T12:03:00Z", "value": 3})
    dash.set_snapshot(moved)
    assert dash.version() != v_prs
    v2 = dash.version()
    dash.notes_path.write_text(json.dumps({"chief": [{"text": "x", "at": "2026-10-24T12:05:00Z"}]}))
    assert dash.version() != v2  # the notes are read per request, so their change is a new version too
    assert v  # a version exists before the first change


class Client:
    """One HTTP/1.1 connection to the page's unix socket, reading the response head."""

    def __init__(self, path, request: str, body: bytes = b""):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect(str(path))
        self.sock.sendall(request.encode() + body)
        self.f = self.sock.makefile("rb")
        self.status = self.f.readline().decode()
        self.headers = {}
        while (line := self.f.readline().decode().strip()):
            k, _, v = line.partition(":")
            self.headers[k.strip().lower()] = v.strip()

    def body(self) -> str:
        return self.f.read(int(self.headers["content-length"])).decode()

    def close(self):
        self.sock.close()


class Socket(Client):
    """A WebSocket client: the upgrade, then the server's JSON text frames."""

    KEY = base64.b64encode(b"0123456789abcdef").decode()

    def __init__(self, path):
        super().__init__(path, "GET /ws HTTP/1.1\r\nHost: page\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                               f"Sec-WebSocket-Key: {self.KEY}\r\nSec-WebSocket-Version: 13\r\n\r\n")

    def frame(self) -> tuple[int, bytes]:
        return server.ws_read_frame(self.f)

    def message(self, kind: str) -> dict:
        """The next JSON message of type KIND."""
        while True:
            opcode, data = self.frame()
            if opcode == 0x1 and (m := json.loads(data))["type"] == kind:
                return m

    def send(self, payload: bytes, opcode: int) -> None:
        mask = b"\x01\x02\x03\x04"
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(struct.pack("!BB", 0x80 | opcode, 0x80 | len(payload)) + mask + masked)


@pytest.fixture
def served(dash, tmp_path):
    path = tmp_path / "page.sock"
    srv = server._UnixHTTPServer(str(path), server.make_handler(dash, proxy_uid=os.getuid()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield path
    srv.shutdown()
    srv.server_close()


def test_socket_pushes_a_new_version_when_the_snapshot_changes(dash, served):
    ws = Socket(served)
    try:
        assert ws.status.split()[1] == "101" and ws.headers["sec-websocket-accept"] == server.ws_accept(Socket.KEY)
        assert ws.message("build")["build"] == server.BUILD  # a page that reconnects to another build reloads itself
        first = ws.message("version")["version"]
        assert first == dash.version()
        assert ws.message("read")["read_at"] == dash.snapshot["read_at"]

        moved = copy.deepcopy(dash.snapshot)
        moved["stream_issues"][0]["title"] = "Task making stream: renamed"
        moved["read_at"] = "2026-10-24T12:05:00Z"
        dash.set_snapshot(moved)
        second = ws.message("version")["version"]
        assert second != first and second == dash.version()
        assert ws.message("read")["read_at"] == "2026-10-24T12:05:00Z"

        # The fragment the page then fetches says which version it renders.
        page = Client(served, "GET /?fragment=1 HTTP/1.1\r\nHost: page\r\n\r\n")
        assert page.status.split()[1] == "200" and page.headers["x-page-version"] == second
        page.close()
    finally:
        ws.close()


def test_socket_pushes_a_new_version_when_a_note_is_written(dash, served, tmp_path):
    ws = Socket(served)
    try:
        first = ws.message("version")["version"]
        dash.notes_path.write_text(json.dumps({"chief": [{"text": "x", "at": "2026-10-24T12:05:00Z"}]}))
        assert ws.message("version")["version"] not in ("", first)
    finally:
        ws.close()


def test_socket_answers_ping_and_close(dash, served):
    ws = Socket(served)
    try:
        ws.message("version")
        ws.send(b"hi", 0x9)
        while (got := ws.frame())[0] != 0xA:
            pass
        assert got == (0xA, b"hi")
        ws.send(struct.pack("!H", 1000), 0x8)
        while (got := ws.frame())[0] != 0x8:
            pass
        assert got == (0x8, struct.pack("!H", 1000))
    finally:
        ws.close()


def test_full_page_carries_its_version_and_connects_a_websocket_with_no_fixed_reload(dash, served):
    page = Client(served, "GET / HTTP/1.1\r\nHost: page\r\n\r\n")
    html = page.body()
    page.close()
    assert f'<main id="page" data-version="{dash.version()}">' in html
    assert "new WebSocket(" in html and "EventSource" not in html
    assert "setInterval(refresh" not in html and "REFRESH_MS" not in html


def test_a_restart_serves_the_last_snapshot_at_once(dash, tmp_path):
    """Each refresh saves the snapshot; a new process loads it, so a page loaded during the 45-115 s first read shows
    the previous read, never "the first read is running"."""
    again = server.Dashboard()
    assert again.snapshot["read_at"] == dash.snapshot["read_at"] and again.version() == dash.version()
    assert "first read is running" not in again.render_v2(False, False)


def test_no_version_is_pushed_before_the_first_read_when_nothing_was_saved(dash, served, tmp_path):
    """A server with no saved snapshot has nothing to show until its first read: a page open across that restart
    keeps what it has, so the first version it hears is the one after that read."""
    import time

    dash.set_snapshot({"read_at": None, "errors": ["the page just started; the first read is running"]})
    ws = Socket(served)
    try:
        time.sleep(0.5)
        dash.set_snapshot(snapshot())
        assert ws.message("version")["version"] == dash.version()
    finally:
        ws.close()

