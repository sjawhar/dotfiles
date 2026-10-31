"""The standup room: Sami's morning department-heads meeting with his chief of staff and the stream leads.

The room is one Dispatch issue (`DASHBOARD_STANDUP_ISSUE`) and one message thread inside it
(`DASHBOARD_STANDUP_ROOT`). Dispatch holds the meeting, so nothing is invented here: a lead posts with
`dispatch message --issue <KEY> --in-reply-to <root>`, receives every line through
`envoy_subscribe notifications.dispatch.issue.<KEY>.>`, and a lead that joins late or restarts reads the whole
meeting with `dispatch read --message <root>`. The room is a meeting, not a second place for asks and answers: a
decision Sami gives in it is written into the issue it belongs to afterwards, by the chief of staff.

Sami's own lines go out twice, both from this page:

1. as a message on the thread, so they are in the record a late reader sees, marked with `SAMI_MARK` so every
   reader knows whose words they are (Dispatch authors a bearer's message as a session, never as a person);
2. over Envoy to every lead's session as `source: human` (`sources.envoy_send`), which agents treat as Sami
   himself.

This page reads the thread from Dispatch every `POLL_SECONDS`, writes it to one file in the state directory, and
that file is watched by the server's version hash, so the existing WebSocket pushes each new line to the browser.
Each line from a lead or the chief of staff is spoken in that speaker's own voice; the audio is synthesised once
and cached on disk under its text, so the same words are never paid for twice.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import render
from .sources import ENVOY_CONFIG, Dispatch, SourceError, envoy_send

log = logging.getLogger(__name__)

ISSUE = os.environ.get("DASHBOARD_STANDUP_ISSUE", "INFRA-460")
ROOT = os.environ.get("DASHBOARD_STANDUP_ROOT", "3dd1d2eb-c975-43d1-ac37-cfa77bca1e42")
CHIEF_SESSION = os.environ.get("DASHBOARD_CHIEF_SESSION", "01a13dca-e979-7264-af32-f654ef57a6f5")
LEADS_FILE = Path(os.environ.get("DASHBOARD_LEADS_FILE", "/mnt/nvme/sami/chief/leads.json"))
PUBLIC_ORIGIN = os.environ.get("DASHBOARD_PUBLIC_ORIGIN", "https://sami-rocqperf.tail12ec1c.ts.net:8446")
DISPATCH_WEB = os.environ.get("DASHBOARD_DISPATCH_WEB", "https://dispatch.tail12ec1c.ts.net")

POLL_SECONDS = float(os.environ.get("DASHBOARD_STANDUP_POLL_SECONDS", "3"))
ACTIVE_SECONDS = 1800  # a room where something was said this recently is a meeting in progress
QUIET_SECONDS = 30  # how often a quiet room is read, so an open page is no standing load on Dispatch
WATCHED_SECONDS = 120  # a room whose page was shown this recently is read at the meeting's pace
MAX_LINE = 20000  # what Sami may type in one line; Dispatch's own message limit is 32000
SPEAK_LIMIT = 4000  # the most text sent to the voice service for one line
# Sami's words are posted under the dashboard's Dispatch bearer, which Dispatch authors as a session. This first
# line says whose words they are, to a lead reading the thread and to Sami reading it in Dispatch's web app.
SAMI_MARK = "Sami Jawhar, in the standup room:"

# The voices a speaker can be given, in the order they are handed out (ElevenLabs, checked 2026-10-26).
VOICES: list[tuple[str, str]] = [
    ("JBFqnCBsd6RMkjVDRZzb", "George"),
    ("EXAVITQu4vr4xnSDxMaL", "Sarah"),
    ("iP95p4xoKVk53GoZ742B", "Chris"),
    ("Xb7hH8MSUJpSbSDYk0k2", "Alice"),
    ("nPczCjzI2devNBz1zQrb", "Brian"),
    ("XrExE9yKIg1WjnnlVkGX", "Matilda"),
    ("cjVigY5qzO86Huf0OWal", "Eric"),
    ("pFZP5JQG7iQjIQuC4Bku", "Lily"),
    ("CwhRBWXzGAHq8TQ4Fs17", "Roger"),
    ("cgSgspJ2msm6clMCkdW9", "Jessica"),
    ("onwK4e9ZLuTAKqWW03F9", "Daniel"),
    ("FGY2WhTYpPnrIDTdsKH5", "Laura"),
]
TTS_MODEL = "eleven_turbo_v2_5"
AUDIO_NAME = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class Line:
    """One thing said in the room."""

    id: str
    at: datetime
    speaker: str  # the name shown and spoken for
    who: str  # the speaker's stable key: a session id or a person's login
    kind: str  # "sami", "chief" or "lead"
    text: str

    @property
    def spoken(self) -> bool:
        """Sami's own lines are not read back to him."""
        return self.kind != "sami" and bool(speakable(self.text))


def parse_time(s: str | None) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def speaker_name(author: dict) -> tuple[str, str]:
    """(name shown, stable key) for a message's author."""
    if author.get("kind") == "user":
        login = str(author.get("id") or "someone")
        return login.split("@")[0], login
    session = str(author.get("id") or "")
    title = ((author.get("origin") or {}).get("session_title") or "").strip()
    if title:
        # Session titles read "👔 CHIEF: chief of staff"; the part before the colon is the short name.
        short = title.split(":")[0].strip()
        return (short or title), session or title
    return (f"session {session[:8]}" if session else "someone"), session


def to_line(msg: dict) -> Line | None:
    at = parse_time(msg.get("created_at"))
    body = msg.get("body")
    if at is None or not isinstance(body, str) or not body.strip():
        return None
    author = msg.get("author") or {}
    name, who = speaker_name(author)
    text = body.strip()
    kind = "lead"
    if text.startswith(SAMI_MARK):
        text = text[len(SAMI_MARK):].strip()
        name, who, kind = "Sami", "sami", "sami"
    elif author.get("kind") == "user":
        kind = "sami"
    elif who == CHIEF_SESSION:
        kind = "chief"
    if not text:
        return None
    return Line(id=str(msg.get("id") or ""), at=at, speaker=name, who=who, kind=kind, text=text)


def read_thread(d: Dispatch, issue: str = ISSUE) -> list[Line]:
    """Every line said on the issue, oldest first: each thread's root and its replies."""
    threads = d.get(f"/api/v1/issues/{urllib.parse.quote(issue)}/messages")
    lines: list[Line] = []
    for t in threads if isinstance(threads, list) else []:
        for msg in [t.get("message"), *(t.get("replies") or [])]:
            if isinstance(msg, dict) and (ln := to_line(msg)):
                lines.append(ln)
    lines.sort(key=lambda ln: (ln.at, ln.id))
    return lines


_MD = [
    (re.compile(r"```.*?```", re.S), " "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), " "),
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),
    (re.compile(r"[*_]{1,3}([^*_]+)[*_]{1,3}"), r"\1"),
    (re.compile(r"^\s*[-*]\s+", re.M), ""),
    (re.compile(r"https?://\S+"), "a link"),
    (re.compile(r"dispatch://\S+"), "a Dispatch link"),
]


def speakable(text: str) -> str:
    """The line as a voice should read it: no markdown marks, no raw URLs, capped."""
    out = text
    for pattern, repl in _MD:
        out = pattern.sub(repl, out)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{2,}", "\n", out).strip()
    return out[:SPEAK_LIMIT]


# ------------------------------------------------------------------------------------------------- voices
class Voices:
    """Which voice each speaker has. Handed out in first-heard order and kept in a file, so a speaker keeps its
    voice across a restart and two speakers never share one while a voice is free."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        try:
            self.map: dict[str, str] = {k: v for k, v in json.loads(path.read_text()).items() if isinstance(v, str)}
        except (OSError, ValueError, AttributeError):
            self.map = {}

    def of(self, who: str) -> str:
        with self.lock:
            if who in self.map:
                return self.map[who]
            taken = set(self.map.values())
            free = [v for v, _ in VOICES if v not in taken]
            voice = free[0] if free else VOICES[len(self.map) % len(VOICES)][0]
            self.map[who] = voice
            try:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.map, indent=1))
                tmp.replace(self.path)
            except OSError:
                log.exception("could not save the standup voices")
            return voice

    def name(self, who: str) -> str:
        voice = self.of(who)
        return next((n for v, n in VOICES if v == voice), voice[:8])


# -------------------------------------------------------------------------------------------------- speech
class ElevenLabs:
    """The voice service. Its key comes from the environment, from a file beside the page's state, or from the
    machine's secrets broker, in that order; the first one that answers is kept for the life of the process."""

    def __init__(self, state_dir: Path, timeout: float = 60) -> None:
        self.state_dir = state_dir
        self.timeout = timeout
        self.lock = threading.Lock()
        self._key: str | None = None

    def key(self) -> str:
        with self.lock:
            if self._key:
                return self._key
            got = os.environ.get("ELEVENLABS_API_KEY", "").strip()
            if not got:
                try:
                    got = (self.state_dir / "elevenlabs.key").read_text().strip()
                except OSError:
                    got = ""
            if not got:
                got = self._from_broker()
            if not got:
                raise SourceError("no ELEVENLABS_API_KEY: not in the environment, the state directory or secretsd")
            self._key = got
            return got

    def _from_broker(self) -> str:
        try:
            r = subprocess.run(
                ["secrets", "ELEVENLABS_API_KEY", "--", "printenv", "ELEVENLABS_API_KEY"],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("the secrets broker could not be asked for ELEVENLABS_API_KEY: %s", exc)
            return ""
        if r.returncode != 0:
            log.warning("the secrets broker refused ELEVENLABS_API_KEY: %s", (r.stderr or "")[:200])
            return ""
        return r.stdout.strip()

    def mp3(self, voice: str, text: str) -> bytes:
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{urllib.parse.quote(voice)}",
            data=json.dumps({"text": text, "model_id": TTS_MODEL}).encode(),
            method="POST",
            headers={"xi-api-key": self.key(), "Content-Type": "application/json", "Accept": "audio/mpeg"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            raise SourceError(f"the voice service answered HTTP {exc.code}: "
                              f"{exc.read()[:200].decode(errors='replace')}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SourceError(f"the voice service could not be reached: {exc}") from exc


# ---------------------------------------------------------------------------------------------------- room
class Room:
    """The meeting: its lines, their audio, and Sami's way of speaking into it."""

    def __init__(self, state_dir: Path, dispatch=None, send=envoy_send, speech=None) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir = self.state_dir / "standup-audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "standup.json"
        self.voices = Voices(self.state_dir / "standup-voices.json")
        self.speech = speech if speech is not None else ElevenLabs(self.state_dir)
        self._dispatch = dispatch
        self._send = send
        self.lock = threading.Lock()
        self.error: str | None = None
        self.read_at: datetime | None = None
        self.lines: list[Line] = self._load()
        self._synth_lock = threading.Lock()
        self.looked: float = 0.0  # when the page was last shown, so a watched room is read at the meeting's pace

    # ------------------------------------------------------------------------------------------ the lines
    def dispatch(self) -> Dispatch:
        if self._dispatch is None:
            self._dispatch = Dispatch()
        return self._dispatch

    def _load(self) -> list[Line]:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        out = []
        for r in raw.get("lines", []) if isinstance(raw, dict) else []:
            at = parse_time(r.get("at"))
            if at is None:
                continue
            out.append(Line(id=r.get("id", ""), at=at, speaker=r.get("speaker", "someone"),
                            who=r.get("who", ""), kind=r.get("kind", "lead"), text=r.get("text", "")))
        return out

    def _save(self) -> None:
        body = {"lines": [{"id": ln.id, "at": ln.at.isoformat(), "speaker": ln.speaker, "who": ln.who,
                           "kind": ln.kind, "text": ln.text} for ln in self.lines]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body))
        tmp.replace(self.path)

    def refresh(self) -> bool:
        """Read the room from Dispatch. True when something was said since the last read."""
        try:
            fresh = read_thread(self.dispatch())
            err = None
        except (SourceError, OSError, ValueError, KeyError) as exc:
            log.warning("could not read the standup room: %s", exc)
            with self.lock:
                self.error = str(exc)[:300]
            return False
        with self.lock:
            changed = [ln.id for ln in fresh] != [ln.id for ln in self.lines] or \
                      [ln.text for ln in fresh] != [ln.text for ln in self.lines]
            self.lines, self.error, self.read_at = fresh, err, datetime.now(UTC)
            if changed:
                try:
                    self._save()
                except OSError:
                    log.exception("could not save the standup room")
        return changed

    def all_lines(self) -> list[Line]:
        with self.lock:
            return list(self.lines)

    def watched(self) -> list[Path]:
        """The files the page reads per request; the server's version hash watches them, so a new line reaches the
        browser over the WebSocket that is already there."""
        return [self.path]

    # ------------------------------------------------------------------------------------------- the audio
    def audio_name(self, line: Line) -> str:
        """The cache name of a line's audio: its voice and the exact words, so the same text is paid for once."""
        voice = self.voices.of(line.who)
        key = f"{voice}\n{TTS_MODEL}\n{speakable(line.text)}".encode()
        return hashlib.sha256(key).hexdigest()[:32]

    def audio_path(self, name: str) -> Path:
        return self.audio_dir / f"{name}.mp3"

    def audio(self, line: Line) -> Path:
        """The line's audio file, synthesised on the first ask and read from disk after that."""
        name = self.audio_name(line)
        path = self.audio_path(name)
        if path.exists() and path.stat().st_size:
            return path
        with self._synth_lock:  # one line at a time: a burst of arrivals must not open ten connections
            if path.exists() and path.stat().st_size:
                return path
            data = self.speech.mp3(self.voices.of(line.who), speakable(line.text))
            if not data:
                raise SourceError("the voice service returned no audio")
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)
        return path

    def prepare(self, lines: list[Line]) -> None:
        """Synthesise what is not cached yet, so a line is speakable the moment it appears."""
        for ln in lines:
            if not ln.spoken or self.audio_path(self.audio_name(ln)).exists():
                continue
            try:
                self.audio(ln)
            except (SourceError, OSError) as exc:
                log.warning("no audio for %s: %s", ln.id, exc)

    # -------------------------------------------------------------------------------------- Sami speaking
    def leads(self) -> list[str]:
        """Every session Sami's line is delivered to: the stream leads and the chief of staff."""
        try:
            got = json.loads(LEADS_FILE.read_text())
        except (OSError, ValueError):
            log.warning("could not read %s; Sami's line goes to the chief of staff only", LEADS_FILE)
            got = []
        out = [s for s in got if isinstance(s, str) and s] if isinstance(got, list) else []
        if CHIEF_SESSION not in out:
            out.append(CHIEF_SESSION)
        return out

    def post(self, text: str) -> dict:
        """Say one line as Sami: write it to the Dispatch thread, then deliver it to every lead as his own words."""
        text = text.strip()
        if not text:
            raise ValueError("the line is empty")
        if len(text) > MAX_LINE:
            raise ValueError(f"the line is {len(text)} characters; the limit is {MAX_LINE}")
        body = f"{SAMI_MARK}\n\n{text}"
        posted = self._post_message(body)
        delivered, missed = self._fan_out(text)
        self.refresh()
        return {"id": posted.get("id", ""), "delivered": delivered, "missed": missed}

    def _post_message(self, body: str) -> dict:
        cfg = json.loads(ENVOY_CONFIG.read_text())["dispatch"]
        server = cfg["serverUrl"].rstrip("/")
        payload = {"body": body, "in_reply_to": ROOT, "actor": {"kind": "session", "id": CHIEF_SESSION}}
        req = urllib.request.Request(
            f"{server}/api/v1/issues/{urllib.parse.quote(ISSUE)}/messages",
            data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                got = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            raise SourceError(f"Dispatch answered HTTP {exc.code}: "
                              f"{exc.read()[:300].decode(errors='replace')}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise SourceError(f"Dispatch could not be reached: {exc}") from exc
        return got.get("message") or got if isinstance(got, dict) else {}

    def _fan_out(self, text: str) -> tuple[int, list[str]]:
        sessions = self.leads()
        message = (
            f"Sami said this in the standup room ({DISPATCH_WEB}/issues/{ISSUE}), sent from his streams dashboard "
            f"({PUBLIC_ORIGIN}/standup):\n\n{text}\n\nThis is Sami's own message; act on it as you would on any "
            f"message from him. Answer in the room: dispatch message --issue {ISSUE} --in-reply-to {ROOT} "
            "--body-file -"
        )
        key = hashlib.sha256(f"{time.time_ns()}{text}".encode()).hexdigest()[:32]
        results: dict[str, bool] = {}

        def one(session: str) -> None:
            try:
                env = self._send(session, message, idempotency_key=f"{key}-{session[:8]}")
                results[session] = bool(env.get("recipient"))
            except Exception as exc:  # recorded on the page, never raised to the reader
                log.warning("the standup line did not reach %s: %s", session, exc)
                results[session] = False

        threads = [threading.Thread(target=one, args=(s,), name="standup-send", daemon=True) for s in sessions]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=25)
        missed = [s for s in sessions if not results.get(s)]
        return len(sessions) - len(missed), missed

    # --------------------------------------------------------------------------------------------- polling
    def wait_seconds(self) -> float:
        """How long to wait before reading Dispatch again: the meeting's pace while someone is watching the page or
        something was said recently, and once every QUIET_SECONDS otherwise, so a room nobody is in is no standing
        load on Dispatch."""
        if time.monotonic() - self.looked < WATCHED_SECONDS:
            return POLL_SECONDS
        lines = self.all_lines()
        if not lines:
            return QUIET_SECONDS
        since = (datetime.now(UTC) - lines[-1].at).total_seconds()
        return POLL_SECONDS if since < ACTIVE_SECONDS else QUIET_SECONDS

    def run_forever(self, stop: threading.Event | None = None) -> None:
        while stop is None or not stop.is_set():
            try:
                if self.refresh():
                    self.prepare(self.all_lines())
            except Exception:
                log.exception("the standup poll failed")
            wait = self.wait_seconds()
            if stop is not None:
                stop.wait(wait)
            else:
                time.sleep(wait)


def start(room: Room) -> threading.Thread:
    t = threading.Thread(target=room.run_forever, name="standup", daemon=True)
    t.start()
    return t


# --------------------------------------------------------------------------------------------------- page
def e(s) -> str:
    return html.escape("" if s is None else str(s))


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_CODE = re.compile(r"`([^`\n]+)`")


def rich(text: str) -> str:
    """The line as the page shows it: escaped, links live, line breaks kept, and the three marks a standup actually
    uses read as what they mean — a heading as its own words, `**Blockers**` in bold, a command as a command."""
    out = render.linkify(_HEADING.sub("", text), DISPATCH_WEB)
    out = _CODE.sub(r"<code>\1</code>", out)
    return _BOLD.sub(r"<b>\1</b>", out).replace("\n", "<br>")


def line_html(room: Room, ln: Line) -> str:
    audio = f' data-audio="/standup/audio/{e(room.audio_name(ln))}.mp3"' if ln.spoken else ""
    voice = f' · {e(room.voices.name(ln.who))}' if ln.spoken else ""
    return (
        f'<article class="ln {e(ln.kind)}" id="ln-{e(ln.id)}" data-mid="{e(ln.id)}"{audio}>'
        f'<div class="meta"><span class="who">{e(ln.speaker)}</span>'
        f'<span class="at">{e(ln.at.strftime("%H:%MZ"))}{voice}</span></div>'
        f'<div class="said">{rich(ln.text)}</div></article>'
    )


def transcript(room: Room) -> str:
    lines = room.all_lines()
    if not lines:
        return ('<div id="transcript" class="tr"><p class="empty">Nothing said yet. The leads post their standups '
                f'into <a href="{e(DISPATCH_WEB)}/issues/{e(ISSUE)}" target="_blank" rel="noopener">{e(ISSUE)}</a>'
                " and each line appears here.</p></div>")
    return f'<div id="transcript" class="tr">{"".join(line_html(room, ln) for ln in lines)}</div>'


def body(room: Room, can_send: bool, notice: str | None = None, notice_error: bool = False) -> str:
    """The room: who is in it, the transcript, and Sami's way of speaking into it."""
    room.looked = time.monotonic()  # someone is in the room: read Dispatch at the meeting's pace
    with room.lock:
        err, read_at = room.error, room.read_at
    bar = f'<div class="notice{" bad-bg" if notice_error else ""}">{e(notice)}</div>' if notice else ""
    if err:
        bar += f'<div class="notice bad-bg">The room could not be read from Dispatch: {e(err)}</div>'
    read = f'read {read_at.strftime("%H:%M:%SZ")}' if read_at else "not read yet"
    disabled = "" if can_send else " disabled"
    why = "" if can_send else '<p class="small dim">Only Sami, signed in over Tailscale, can speak here.</p>'
    return (
        '<header class="top">'
        '<div class="when"><h1>Standup room</h1>'
        f'<span class="date">The morning department-heads meeting · {e(read)}</span>'
        f'<span class="date"><a href="{e(DISPATCH_WEB)}/issues/{e(ISSUE)}" target="_blank" rel="noopener">'
        f'{e(ISSUE)} in Dispatch</a> is the record · <a href="/">Streams</a></span></div>'
        '<div class="facts"><button type="button" id="join" class="btn join">Join the call</button>'
        '<button type="button" id="pause" class="btn" hidden>Pause</button>'
        '<button type="button" id="skip" class="btn" hidden>Skip</button>'
        '<span id="playing" class="small dim"></span></div>'
        "</header>"
        f"{bar}"
        f"{transcript(room)}"
        '<form class="say" id="say" method="post" action="/standup">'
        f'<textarea id="line" name="text" rows="4" maxlength="{MAX_LINE}" placeholder="Say something to the '
        f'leads&hellip;"{disabled}></textarea>'
        '<div class="row"><button type="submit" class="btn send"'
        f'{disabled}>Send as Sami</button>'
        f'<button type="button" id="talk" class="btn"{disabled}>Hold to talk</button>'
        '<span id="saystate" class="small dim"></span></div>'
        f"{why}</form>"
    )


CSS = """
:root{--fs-meta:12px;--fs-body:14px;--fs-title:17px;--fs-num:28px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:400 var(--fs-body)/1.5 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-text-size-adjust:100%}
main{max-width:860px;margin:0 auto;padding:14px 20px 220px}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
h1{font-size:var(--fs-title);font-weight:600;margin:0}
.small{font-size:var(--fs-meta)}
.dim{color:var(--dim)}
.top{display:flex;flex-wrap:wrap;gap:10px;justify-content:space-between;align-items:flex-start;
border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:12px}
.when{display:flex;flex-direction:column;gap:2px}
.date{font-size:var(--fs-meta);color:var(--dim)}
.facts{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.btn{font:600 var(--fs-meta)/1 inherit;padding:8px 12px;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--ink);cursor:pointer}
.btn:hover:not(:disabled){border-color:var(--blue);color:var(--blue)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn.join,.btn.send{background:var(--blue);border-color:var(--blue);color:#fff}
.btn.join:hover:not(:disabled),.btn.send:hover:not(:disabled){color:#fff;opacity:.9}
.btn[aria-pressed=true]{background:var(--badbg);border-color:var(--bad);color:var(--bad)}
.notice{margin:8px 0;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--card);
font-size:var(--fs-meta)}
.bad-bg{background:var(--badbg);border-color:var(--bad);color:var(--bad)}
.tr{display:flex;flex-direction:column;gap:10px}
.ln{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:9px 12px}
.ln.sami{border-color:var(--blue);background:var(--card)}
.ln.chief{border-left:3px solid var(--grey)}
.ln .meta{display:flex;gap:8px;align-items:baseline;margin-bottom:3px}
.ln .who{font-weight:600}
.ln.sami .who{color:var(--blue)}
.ln .at{font-size:var(--fs-meta);color:var(--dim)}
.ln .said{white-space:normal;overflow-wrap:anywhere}
.ln code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:var(--fs-meta);
background:var(--greybg);border-radius:4px;padding:1px 4px}
.ln.speaking{box-shadow:0 0 0 2px var(--blue)}
.empty{color:var(--dim);font-size:var(--fs-meta)}
.say{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);
padding:10px 20px 12px;z-index:8}
.say>*{max-width:820px;margin:0 auto}
.say textarea{display:block;width:100%;min-height:68px;resize:vertical;font:400 var(--fs-body)/1.45 inherit;
padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink)}
.say .row{display:flex;gap:8px;align-items:center;margin-top:8px}
@media (max-width:520px){main{padding:10px 12px 240px}.say{padding:8px 12px 10px}.top{gap:6px}}
"""

SCRIPT = """
(function(){
  // Live: the server pushes a version over the WebSocket that is already there (/ws). On a new version this page
  // fetches its own transcript and replaces it. Nothing the reader is typing lives inside the transcript, so a
  // replacement loses nothing; the scroll stays at the bottom only when it was already there.
  const tr = () => document.getElementById('transcript');
  const audio = new Audio();
  const spoken = new Set();
  let joined = false, queue = [], now = null, paused = false, first = true;
  const say = (t) => { const el = document.getElementById('saystate'); if (el) el.textContent = t; };
  const playing = (t) => { const el = document.getElementById('playing'); if (el) el.textContent = t; };

  function seen(){ return [...tr().querySelectorAll('[data-mid]')]; }
  function mark(){ for (const el of seen()) spoken.add(el.dataset.mid); }
  function enqueue(){
    if (!joined) { mark(); return; }
    for (const el of seen()) {
      const id = el.dataset.mid;
      if (spoken.has(id)) continue;
      spoken.add(id);
      if (el.dataset.audio) queue.push({id, url: el.dataset.audio, who: el.querySelector('.who').textContent});
    }
    next();
  }
  function next(){
    if (now || paused || !queue.length) { if (!now && !queue.length) playing(''); return; }
    now = queue.shift();
    const el = document.getElementById('ln-' + now.id);
    if (el) el.classList.add('speaking');
    playing(now.who + ' is speaking' + (queue.length ? ' · ' + queue.length + ' waiting' : ''));
    audio.src = now.url;
    audio.play().catch(err => { playing('audio failed: ' + err.message); done(); });
  }
  function done(){
    if (now) { const el = document.getElementById('ln-' + now.id); if (el) el.classList.remove('speaking'); }
    now = null; next();
  }
  audio.addEventListener('ended', done);
  audio.addEventListener('error', () => { playing('audio could not be loaded'); done(); });

  document.getElementById('join').addEventListener('click', ev => {
    joined = true; paused = false;
    ev.target.textContent = 'In the call';
    ev.target.disabled = true;
    for (const b of ['pause','skip']) document.getElementById(b).hidden = false;
    audio.play().catch(() => {});  // the one click that lets this page play sound
    playing('listening for the next line');
  });
  document.getElementById('pause').addEventListener('click', ev => {
    paused = !paused;
    ev.target.textContent = paused ? 'Resume' : 'Pause';
    if (paused) { audio.pause(); playing('paused' + (queue.length ? ' · ' + queue.length + ' waiting' : '')); }
    else if (now) { audio.play().catch(() => {}); playing(now.who + ' is speaking'); }
    else next();
  });
  document.getElementById('skip').addEventListener('click', () => { audio.pause(); paused = false;
    document.getElementById('pause').textContent = 'Pause'; done(); });

  function atBottom(){ return window.innerHeight + window.scrollY >= document.body.scrollHeight - 120; }
  async function pull(){
    const bottom = atBottom();
    try {
      const r = await fetch('/standup?fragment=1', {cache: 'no-store'});
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const fresh = document.createElement('div');
      fresh.innerHTML = await r.text();
      const got = fresh.querySelector('#transcript');
      if (got && got.innerHTML !== tr().innerHTML) {
        tr().replaceWith(got);
        enqueue();
        if (bottom) window.scrollTo(0, document.body.scrollHeight);
      }
    } catch (err) { playing('the room could not be read: ' + err.message); }
  }

  // Sending: the form posts and the page stays, so what he types is never lost to a reload.
  const form = document.getElementById('say'), box = document.getElementById('line');
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    const text = box.value.trim();
    if (!text) return;
    const btn = form.querySelector('.send'); btn.disabled = true; say('sending…');
    try {
      const r = await fetch('/standup', {method: 'POST', headers: {'Content-Type':
        'application/x-www-form-urlencoded', 'Accept': 'application/json'},
        body: new URLSearchParams({text})});
      const got = await r.json();
      say(got.message || (r.ok ? 'sent' : 'not sent'));
      if (got.ok) { box.value = ''; pull(); }
    } catch (err) { say('not sent: ' + err.message); }
    btn.disabled = false; box.focus();
  });
  box.addEventListener('keydown', ev => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key === 'Enter') { ev.preventDefault();
      form.dispatchEvent(new Event('submit')); }
  });

  // Push to talk: the browser's own dictation, no server in the way. Chrome and Safari only.
  const talk = document.getElementById('talk');
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Rec) { talk.disabled = true; talk.title = 'dictation needs Chrome or Safari'; }
  else {
    let rec = null, base = '';
    const stop = () => { if (rec) { rec.stop(); rec = null; } talk.setAttribute('aria-pressed', 'false');
      talk.textContent = 'Hold to talk'; };
    talk.addEventListener('click', () => {
      if (rec) { stop(); return; }
      rec = new Rec(); rec.continuous = true; rec.interimResults = true; rec.lang = 'en-US';
      base = box.value ? box.value.replace(/\\s*$/, ' ') : '';
      rec.onresult = ev => {
        let text = '';
        for (let i = 0; i < ev.results.length; i++) text += ev.results[i][0].transcript;
        box.value = base + text;
      };
      rec.onerror = ev => { say('dictation: ' + ev.error); stop(); };
      rec.onend = () => { if (rec) { rec = null; talk.setAttribute('aria-pressed', 'false');
        talk.textContent = 'Hold to talk'; } };
      rec.start();
      talk.setAttribute('aria-pressed', 'true'); talk.textContent = 'Stop talking';
      say('listening — speak, then press Stop');
    });
  }

  // The live channel, the same one the front page uses.
  let ws = null, delay = 1000, heard = Date.now();
  function connect(){
    if (ws) { ws.onclose = null; try { ws.close(); } catch (e) {} }
    const sock = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
    ws = sock;
    sock.onopen = () => { delay = 1000; heard = Date.now(); };
    sock.onmessage = ev => {
      heard = Date.now();
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.type === 'version') pull();
    };
    sock.onclose = () => { if (ws !== sock) return; ws = null; setTimeout(connect, delay);
      delay = Math.min(delay * 2, 30000); };
  }
  setInterval(() => { if (Date.now() - heard > 45000) { heard = Date.now(); connect(); } }, 5000);
  setInterval(pull, 10000);  // a belt for the braces: the room is never more than 10 s stale
  if (window.WebSocket) connect();
  mark();  // what was already on the page when he opened it is not read back to him
  if (first) { first = false; window.scrollTo(0, document.body.scrollHeight); }
})();
"""


def page_html(inner: str, version: str = "") -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Standup room</title><style>{render.THEME}{CSS}</style></head><body>"
        f'<main id="page" data-version="{e(version)}">{inner}</main>'
        f"<script>{SCRIPT}</script></body></html>"
    )
