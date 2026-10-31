"""The page's HTTP server on a unix socket, which `tailscale serve --https=8446 unix:<path>` publishes to the tailnet.

Reads (`GET /`) are open to anyone on the tailnet. Acting as Sami is not: a note to a lead (`POST /note`) and the
"last look" record are accepted only when the socket's peer is tailscaled (uid 0, via SO_PEERCRED: every agent here
runs as the same user, so only root can be the proxy), its Tailscale-User-Login header is Sami's, and, for a POST,
the Origin is this page's own. Which of his devices he uses does not matter."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import socket
import socketserver
import struct
import threading
import time
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from . import model, render, rounds, standup, v2_model, v2_render, videos
from .collect import VersionCache, collect
from .prs import PrCache
from .sources import Dispatch, envoy_send
from .videos import VideoStore

log = logging.getLogger(__name__)

PUBLIC_ORIGIN = os.environ.get("DASHBOARD_PUBLIC_ORIGIN", "https://sami-rocqperf.tail12ec1c.ts.net:8446")
LOGINS = tuple(x for x in os.environ.get("DASHBOARD_LOGINS", "sami@theorem.dev").split(",") if x)
REFRESH_SECONDS = int(os.environ.get("DASHBOARD_REFRESH_SECONDS", "120"))
STATE_DIR = Path(os.environ.get("DASHBOARD_STATE_DIR", Path.home() / ".local" / "state" / "chief-dashboard"))
VISIT_GAP = timedelta(minutes=30)
MAX_NOTE = render.MAX_NOTE  # characters; the page's note boxes carry the same number as their `maxlength`
CHIEF = "chief"  # the note target of the chief-of-staff panel
CHIEF_SESSION = os.environ.get("DASHBOARD_CHIEF_SESSION", v2_render.CHIEF_SESSION)
PING_SECONDS = 15  # a /ws connection that has said nothing for this long sends a ping
WATCH_SECONDS = 5  # how often a /ws connection re-checks the files the page reads per request
# The page's own code: a page that reconnects after a deploy with other CSS or JavaScript reloads itself.
BUILD = hashlib.sha256(
    (render.THEME + render.CSS + render.SCRIPT + render.LIVE_CSS + v2_render.CSS + v2_render.STUDIO_SCRIPT).encode()
).hexdigest()[:12]


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds").replace("+00:00", "Z")


# What Sami asked the chief of staff; each entry links to its answer in Dispatch (the answers live only there).
ASKS_LEDGER = Path(os.environ.get("DASHBOARD_ASKS_LEDGER", "/mnt/nvme/sami/chief/asks-ledger.json"))


def load_asks() -> list[dict] | None:
    """The ledger's `asks`, read on every request; None when it cannot be read."""
    try:
        asks = json.loads(ASKS_LEDGER.read_text()).get("asks")
    except (OSError, ValueError, AttributeError):
        return None
    return asks if isinstance(asks, list) else None


# Fields of a live session that move on every heartbeat; the front page shows none of them.
_HEARTBEAT = ("last_seen", "last_activity")


def snapshot_digest(snap: dict) -> str:
    """The snapshot's content as a hash, without the times of its reads or the live sessions' heartbeats: a refresh
    that found nothing new keeps it."""
    body = {k: v for k, v in snap.items() if k != "read_at"}
    if isinstance(body.get("prs"), dict):
        body["prs"] = {k: v for k, v in body["prs"].items() if k != "read_at"}
    if isinstance(body.get("agents"), dict):
        body["agents"] = {sid: {k: v for k, v in a.items() if k not in _HEARTBEAT} if isinstance(a, dict) else a
                          for sid, a in body["agents"].items()}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def snapshot_path() -> Path:
    return STATE_DIR / "snapshot.json"


def load_snapshot() -> dict | None:
    try:
        snap = json.loads(snapshot_path().read_text())
    except (OSError, ValueError):
        return None
    return snap if isinstance(snap, dict) and snap.get("read_at") else None


def save_snapshot(snap: dict) -> None:
    path = snapshot_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, default=str))
        tmp.replace(path)
    except OSError:
        log.exception("could not save the snapshot")


# WebSocket (RFC 6455), the few parts the page needs: the handshake, unmasked text frames from the server, and
# reading the browser's masked control frames (close, ping) so a closed tab ends its connection.
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_MAX_FRAME = 1 << 16  # the page sends only control frames; anything larger ends the connection


def ws_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def ws_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return head + payload


def ws_read_frame(rfile) -> tuple[int, bytes]:
    """(opcode, payload) of the next frame; EOFError when the peer closed, ValueError on a frame too large."""
    head = rfile.read(2)
    if len(head) < 2:
        raise EOFError
    opcode, n = head[0] & 0x0F, head[1] & 0x7F
    if n == 126:
        n = struct.unpack("!H", rfile.read(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", rfile.read(8))[0]
    if n > WS_MAX_FRAME:
        raise ValueError("frame too large")
    mask = rfile.read(4) if head[1] & 0x80 else b""
    data = rfile.read(n)
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return opcode, data


class Dashboard:
    def __init__(self) -> None:
        self.cache = VersionCache()
        self.prs = PrCache()
        self.videos = VideoStore(STATE_DIR / "videos")
        # The last snapshot is saved on every refresh and loaded here, so a restart serves the page at once from the
        # previous read instead of "the first read is running" for the 45-115 s a read takes (Sami, 2026-10-26).
        self.snapshot: dict = load_snapshot() or {"read_at": None,
                                                  "errors": ["the page just started; the first read is running"]}
        if "dispatch_web" not in self.snapshot:
            try:  # so the Inbox link works before the first read finishes
                self.snapshot["dispatch_web"] = Dispatch().server
            except (OSError, KeyError, ValueError):
                pass
        self.lock = threading.Lock()
        # Woken when a refresh stores a snapshot, so each open /ws connection checks for a new version at once.
        self.changed = threading.Condition()
        self.snap_digest = snapshot_digest(self.snapshot)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.looks_path = STATE_DIR / "looks.json"
        self.notes_path = STATE_DIR / "notes.jsonl"
        # The standup room (/standup): Dispatch holds the meeting; this reads it and keeps one file here, which
        # `watched_files` puts in the page version, so each new line reaches the browser over the WebSocket.
        self.room = standup.Room(STATE_DIR)

    # ----------------------------------------------------------------------------------------------- data
    def refresh(self) -> None:
        t0 = time.monotonic()
        snap = collect(self.cache, videos=self.videos, prs=self.prs)
        self.set_snapshot(snap)
        log.info("refreshed in %.1fs, %d errors", time.monotonic() - t0, len(snap["errors"]))

    def set_snapshot(self, snap: dict) -> None:
        digest = snapshot_digest(snap)
        with self.lock:
            self.snapshot = snap
            self.snap_digest = digest
        save_snapshot(snap)
        with self.changed:
            self.changed.notify_all()

    def watched_files(self) -> list[Path]:
        """The files the front page reads on every request, outside the snapshot."""
        return [self.notes_path, ASKS_LEDGER, *rounds.watched(), *self.room.watched()]

    def version(self) -> str:
        """What the page shows, as one short hash: the snapshot without its read times, and the size and change time
        of each file the page reads per request. Equal versions render the same page (relative ages aside)."""
        with self.lock:
            h = hashlib.sha256(self.snap_digest.encode())
        for p in self.watched_files():
            try:
                st = p.stat()
                h.update(f"{p}:{st.st_mtime_ns}:{st.st_size};".encode())
            except OSError:
                h.update(f"{p}:absent;".encode())
        return h.hexdigest()[:16]

    def read_at(self) -> str | None:
        with self.lock:
            return self.snapshot.get("read_at")

    def wait_change(self, timeout: float) -> None:
        with self.changed:
            self.changed.wait(timeout)

    def run_forever(self) -> None:
        while True:
            try:
                self.refresh()
            except Exception:
                log.exception("refresh failed")
            time.sleep(REFRESH_SECONDS)

    # ---------------------------------------------------------------------------------------------- looks
    def _looks(self) -> dict[str, list[str]]:
        try:
            return json.loads(self.looks_path.read_text())
        except (OSError, ValueError):
            return {}

    def previous_visit(self, login: str, now: datetime, record: bool) -> datetime | None:
        """The last page load before the current visit (loads less than 30 min apart are one visit)."""
        with self.lock:
            looks = self._looks()
            times = sorted(t for t in (model.parse_time(x) for x in looks.get(login, [])) if t)
            run_start = now
            prev = None
            for t in reversed(times):
                if run_start - t <= VISIT_GAP:
                    run_start = t
                else:
                    prev = t
                    break
            if record:
                looks[login] = [iso(t) for t in times[-200:]] + [iso(now)]
                tmp = self.looks_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(looks))
                tmp.replace(self.looks_path)
            return prev

    # ---------------------------------------------------------------------------------------------- notes
    def notes(self) -> dict[str, list[dict]]:
        merged: dict[str, dict] = {}
        try:
            lines = self.notes_path.read_text().splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            merged.setdefault(rec["id"], {}).update(rec)
        out: dict[str, list[dict]] = {}
        for rec in merged.values():
            out.setdefault(rec.get("stream", ""), []).append(rec)
        return out

    def _append_note(self, rec: dict) -> None:
        with self.lock, self.notes_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def send_note(self, slug: str, text: str, login: str) -> dict:
        """Send Sami's note over Envoy to a stream's lead (`slug`), or to the chief of staff (`slug` == CHIEF), and
        record it in notes.jsonl with its delivery."""
        text = text.strip()
        if not text:
            raise ValueError("the note is empty")
        if len(text) > MAX_NOTE:
            raise ValueError(f"the note is {len(text)} characters; the limit is {MAX_NOTE}")
        if slug == CHIEF:
            session, title = CHIEF_SESSION, "the chief of staff"
            message = (f"Note from Sami to you as his chief of staff, sent from his streams dashboard ({PUBLIC_ORIGIN}/):"
                       f"\n\n{text}\n\nThis is Sami's own message; act on it as you would on any message from him.")
        else:
            # The lead is the one the stream's own scoreboard names (else the session claiming its issue), as last read.
            with self.lock:
                snap = self.snapshot
            st = next((x for x in model.build_streams(snap, now_utc()) if x.slug == slug), None)
            if st is None:
                raise ValueError(f"no stream {slug!r} on the page")
            if not st.lead_session:
                raise ValueError(f"{st.name} names no lead session in its scoreboard, and no session claims {st.issue}")
            session, title = st.lead_session, st.lead_title
            message = (
                f"Note from Sami about the {st.name} stream you lead, sent from his streams dashboard "
                f"({PUBLIC_ORIGIN}/):\n\n{text}\n\nThis is Sami's own message; act on it as you would on any message "
                "from him."
            )
        rec = {
            "id": str(uuid.uuid4()),
            "at": iso(now_utc()),
            "stream": slug,
            "login": login,
            "session_id": session,
            "session_title": title,
            "text": text,
            "delivery": "pending",
        }
        self._append_note(rec)
        try:
            env = envoy_send(session, message, idempotency_key=rec["id"])
            update = {"delivery": "delivered" if env.get("recipient") else "not delivered", "recipient": env.get("recipient")}
            if not env.get("recipient"):
                update["error"] = f"{title}'s session is not listening on Envoy now"
        except Exception as e:  # recorded on the page, never raised to the reader
            if "no live session" in str(e):
                why = f"{title}'s session is not running on Envoy now; send again once it is, or comment in Dispatch"
            else:
                why = str(e)[:300]
            update = {"delivery": "not delivered", "error": why}
        update["delivery_at"] = iso(now_utc())
        self._append_note({"id": rec["id"], **update})
        return {**rec, **update}

    # --------------------------------------------------------------------------------------------- render
    def render(self, since: datetime, label: str, window: str, can_send: bool, notice=None, notice_error=False) -> str:
        with self.lock:
            snap = self.snapshot
        now = now_utc()
        page = model.build(snap, now, since, label)
        return render.body(page, snap.get("dispatch_web", ""), self.notes(), window, can_send, notice, notice_error)

    def render_v2(self, can_send: bool, example: bool, notice=None, notice_error=False) -> str:
        """The front page: the header, the chief-of-staff panel (a note box and a link into its live conversation),
        then the streams. Asks and answers live only in Dispatch (Sami, 2026-10-26 04:53Z: "Please, let's just use
        dispatch, which already has all of this. Please do not invent a new one.")."""
        with self.lock:
            snap = self.snapshot
        now = now_utc()
        page = v2_model.build(snap, now, example=example)
        notes = self.notes()
        running = CHIEF_SESSION in (snap.get("agents") or {})
        chief = v2_render.chief_panel(page, notes.get(CHIEF, []), can_send, running, CHIEF_SESSION, load_asks())
        loop = v2_render.loop_section(rounds.load())
        return v2_render.body(page, snap.get("explainers") or {}, notice, notice_error, chief=chief, loop=loop,
                              notes=notes, can_send=can_send)


def peer_uid(conn: socket.socket) -> int:
    _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    return uid


def make_handler(dash: Dashboard, proxy_uid: int = 0):
    class Handler(BaseHTTPRequestHandler):
        timeout = 30

        def log_message(self, fmt, *args):
            log.info("uid %s %s", peer_uid(self.connection), fmt % args)

        def _send(self, status: int, body: str, ctype: str = "text/html; charset=utf-8", headers=None) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _ws(self) -> None:
            """The page's live updates, over a WebSocket (`/ws`). The server sends JSON messages: `build` (the page's
            own code, `BUILD`) on connect; `version` (`Dashboard.version`) on connect and whenever it changes, never
            before the first read after a start with no saved snapshot; `read` with the snapshot's read time after each
            refresh; and `ping` every 15 s, so the page can tell a silently dropped connection. The page fetches its
            fragment only on a new version, and reloads itself (once nothing plays and nothing is typed) when it
            reconnects to a different build. A thread reads the browser's frames: close ends the connection, ping gets
            a pong."""
            key = self.headers.get("Sec-WebSocket-Key")
            if (self.headers.get("Upgrade") or "").lower() != "websocket" or not key:
                self._send(400, "expected a WebSocket upgrade", "text/plain")
                return
            self.close_connection = True
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", ws_accept(key))
            self.end_headers()
            self.wfile.flush()
            self.connection.settimeout(None)  # a quiet page is normal; a dead one fails the next ping's write
            lock, closed = threading.Lock(), threading.Event()

            def send(payload: bytes, opcode: int = 0x1) -> None:
                with lock:
                    self.wfile.write(ws_frame(payload, opcode))
                    self.wfile.flush()

            def reader() -> None:
                try:
                    while not closed.is_set():
                        opcode, data = ws_read_frame(self.rfile)
                        if opcode == 0x8:
                            send(data[:2], 0x8)
                            return
                        if opcode == 0x9:
                            send(data, 0xA)
                except (EOFError, ValueError, OSError, struct.error):
                    pass
                finally:
                    closed.set()

            threading.Thread(target=reader, name="ws-reader", daemon=True).start()
            sent_version = sent_read = None
            last_write = 0.0
            try:
                send(json.dumps({"type": "build", "build": BUILD}).encode())
                while not closed.is_set():
                    msgs = []
                    v, r = dash.version(), dash.read_at()
                    if r is not None and v != sent_version:
                        msgs.append({"type": "version", "version": v})
                        sent_version = v
                    if r != sent_read:
                        msgs.append({"type": "read", "read_at": r})
                        sent_read = r
                    if not msgs and time.monotonic() - last_write >= PING_SECONDS:
                        msgs.append({"type": "ping"})
                    for m in msgs:
                        send(json.dumps(m).encode())
                    if msgs:
                        last_write = time.monotonic()
                    dash.wait_change(WATCH_SECONDS)
            except OSError:
                pass  # the page went away; it reconnects by itself
            finally:
                closed.set()

        def _who(self) -> tuple[str | None, str | None]:
            """(why this request may not act as Sami or None, his login)."""
            uid = peer_uid(self.connection)
            if uid != proxy_uid:
                return f"this request did not come through tailscale serve (peer uid {uid})", None
            login = self.headers.get("Tailscale-User-Login")
            if login not in LOGINS:
                return f"only {', '.join(LOGINS)} may act here (this request: {login})", None
            return None, login

        def _video(self, sha: str, head: bool) -> None:
            """A stream's video with Range support (iPhone Safari plays a video only through Range requests)."""
            path = dash.videos.path(sha)
            if path is None:
                self._send(404, "no such video", "text/plain")
                return
            size = path.stat().st_size
            rng = videos.parse_range(self.headers.get("Range"), size)
            if self.headers.get("Range") and rng is None:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            first, last = rng or (0, size - 1)
            self.send_response(206 if rng else 200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(last - first + 1))
            if rng:
                self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")  # named by its sha256
            self.end_headers()
            if not head:
                try:
                    videos.copy_range(path, self.wfile, first, last)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the player closed the connection to seek; normal

        def do_HEAD(self):
            m = re.fullmatch(r"/video/([0-9a-f]{64})\.mp4", urllib.parse.urlsplit(self.path).path)
            if m:
                self._video(m.group(1), head=True)
            else:
                self._send(404, "", "text/plain")

        def _standup_audio(self, name: str) -> None:
            """One line of the standup in its speaker's voice: synthesised on the first ask, read from disk after."""
            path = dash.room.audio_path(name)
            if not path.exists():
                line = next((ln for ln in dash.room.all_lines()
                             if ln.spoken and dash.room.audio_name(ln) == name), None)
                if line is None:
                    self._send(404, "no line of the standup has this audio", "text/plain")
                    return
                try:
                    path = dash.room.audio(line)
                except Exception as exc:
                    log.warning("no standup audio for %s: %s", name, exc)
                    self._send(503, f"no audio: {exc}", "text/plain")
                    return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")  # named by its own text
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the player went away

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            if m := re.fullmatch(r"/video/([0-9a-f]{64})\.mp4", url.path):
                self._video(m.group(1), head=False)
                return
            if url.path == "/healthz":
                with dash.lock:
                    read_at = dash.snapshot.get("read_at")
                self._send(200, json.dumps({"read_at": read_at}), "application/json")
                return
            if url.path == "/ws":
                self._ws()
                return
            if url.path.startswith("/rounds/"):
                # The learning loop's round files, read-only, by exact name (no path can leave the rounds folder).
                name = url.path.removeprefix("/rounds/")
                path = rounds.ROUNDS / name
                if not rounds.FILE_NAME.match(name) or not path.is_file():
                    self._send(404, "no such round file", "text/plain")
                    return
                self._send(200, path.read_text(errors="replace"), "text/plain; charset=utf-8")
                return
            if url.path == "/standup":
                # The standup room: the meeting lives in Dispatch; this page shows it and lets Sami speak into it.
                refusal, _login = self._who()
                version = dash.version()
                notice = q.get("sent") or q.get("error")
                inner = standup.body(dash.room, refusal is None, notice, "error" in q)
                page = inner if q.get("fragment") == "1" else standup.page_html(inner, version)
                self._send(200, page, headers={"X-Page-Version": version})
                return
            if m := re.fullmatch(r"/standup/audio/([0-9a-f]{32})\.mp3", url.path):
                self._standup_audio(m.group(1))
                return
            # The new layout is the front page; /v2 stays as its old address. The previous page lives at /v1.
            if url.path in ("/", "/v2"):
                refusal, _login = self._who()
                fragment = q.get("fragment") == "1"
                version = dash.version()  # before the render: a change during it makes the page fetch again
                notice = q.get("sent") or q.get("error")
                inner = dash.render_v2(refusal is None, q.get("example") == "1", notice, "error" in q)
                body = inner if fragment else v2_render.page_html(inner, "Streams", version)
                self._send(200, body, headers={"X-Page-Version": version})
                return
            if url.path != "/v1":
                self._send(404, "not found", "text/plain")
                return
            now = now_utc()
            refusal, login = self._who()
            window = q.get("window", "last")
            fragment = q.get("fragment") == "1"
            if window == "24h":
                since, label = now - timedelta(hours=24), "in the last 24 h"
            elif window == "7d":
                since, label = now - timedelta(days=7), "in the last 7 days"
            else:
                window = "last"
                given = model.parse_time(q.get("since")) if fragment else None
                prev = given or (dash.previous_visit(login, now, record=not fragment) if login else None)
                if prev is None:
                    since, label = now - timedelta(hours=24), "in the last 24 h (no earlier visit of yours recorded)"
                else:
                    since, label = prev, f"since your last look ({render.when(prev, now)})"
            notice = q.get("sent") or q.get("error")
            version = dash.version()
            inner = dash.render(since, label, window, refusal is None, notice, "error" in q)
            body = inner if fragment else render.page_html(inner, iso(since), version)
            self._send(200, body, headers={"X-Page-Version": version})

        def _standup_post(self, form: dict, wants_json: bool) -> None:
            """Sami's line in the standup room: written to the Dispatch thread, then delivered to every lead as
            his own words over Envoy."""
            try:
                r = dash.room.post(form.get("text", ""))
                n = r["delivered"]
                msg = (f"Said in the room, and delivered to {n} session{'' if n == 1 else 's'} as your own words"
                       + (f"; {len(r['missed'])} not listening now." if r["missed"] else "."))
                ok = True
            except Exception as exc:
                log.exception("the standup line failed")
                msg, ok = f"Not sent: {exc}", False
            if wants_json:
                self._send(200 if ok else 500, json.dumps({"ok": ok, "message": msg}), "application/json")
                return
            self.send_response(303)
            self.send_header("Location", "/standup?" + urllib.parse.urlencode({"sent" if ok else "error": msg}))
            self.end_headers()

        def do_POST(self):
            """`/note`, unchanged for the plain form: it answers 303 back to the page. The front page's note field
            posts the same body with `Accept: application/json` and gets {ok, message} instead, so sending a note
            neither reloads the page nor stops the video he is watching. `/standup` is the same shape: Sami's line
            in the standup room, answered as JSON to the room's own page."""
            if self.path not in ("/note", "/standup"):
                self._send(404, "not found", "text/plain")
                return
            wants_json = "application/json" in (self.headers.get("Accept") or "")
            refusal, login = self._who()
            if refusal is None and self.headers.get("Origin") != PUBLIC_ORIGIN:
                refusal = f"the request's Origin {self.headers.get('Origin')!r} is not this page ({PUBLIC_ORIGIN})"
            if refusal:
                if wants_json:
                    self._send(403, json.dumps({"ok": False, "message": f"refused: {refusal}"}), "application/json")
                else:
                    self._send(403, f"refused: {refusal}", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._send(411, "a Content-Length is required", "text/plain")
                return
            # A form-encoded line can take up to 12 bytes per character (a 4-byte character, percent-escaped), plus
            # the hidden fields; past that it is not a note or a standup line this page sent.
            cap = standup.MAX_LINE if self.path == "/standup" else MAX_NOTE
            if not 0 <= length <= cap * 12 + 4096:
                self._send(413, "too long", "text/plain")
                return
            form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(length).decode(errors="replace")).items()}
            if self.path == "/standup":
                self._standup_post(form, wants_json)
                return
            slug = form.get("stream", "")
            ok = False
            try:
                r = dash.send_note(slug, form.get("text", ""), login)
                who = r.get("session_title") or r["session_id"]
                if r["delivery"] == "delivered":
                    ok = True
                    qs = {"sent": f"Note delivered to {who}."}
                else:
                    qs = {"error": f"Note not delivered to {who}: {r.get('error')}. It is recorded on the stream's card."}
            except Exception as e:
                log.exception("note failed")
                qs = {"error": f"Note not sent: {e}"}
            if wants_json:
                self._send(200, json.dumps({"ok": ok, "message": qs.get("sent") or qs["error"]}), "application/json")
                return
            back = form.get("back") if form.get("back") in ("/", "/v1", "/v2") else "/"
            self.send_response(303)
            self.send_header("Location", f"{back}?{urllib.parse.urlencode(qs)}#s-{urllib.parse.quote(slug)}")
            self.end_headers()

    return Handler


class _UnixHTTPServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def serve(socket_path: Path) -> None:
    dash = Dashboard()
    threading.Thread(target=dash.run_forever, name="refresh", daemon=True).start()
    standup.start(dash.room)  # reads the standup room from Dispatch and keeps its file fresh
    socket_path.unlink(missing_ok=True)
    server = _UnixHTTPServer(str(socket_path), make_handler(dash))
    os.chmod(socket_path, 0o660)
    log.info("streams dashboard on unix:%s", socket_path)
    server.serve_forever()
