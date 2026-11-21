"""The page's two outside services, read with the machine's Envoy config (~/.config/legion/envoy.json):
Dispatch's REST API (issues, scoreboard files, open asks, live sessions) and the Envoy hub (a note to a lead).

Every request to Dispatch first runs the dotfiles' scripts/agent-private-guard with the bearer it is about to
send (`private_guard`): a bearer that lists a private project is refused, and so is one Dispatch cannot vouch
for, and the command then stops with exit 2 (the guard's header has the rule)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENVOY_CONFIG = Path(os.environ.get("ENVOY_CONFIG_FILE", Path.home() / ".config" / "legion" / "envoy.json"))
# The guard of the dotfiles checkout this package is in.
GUARD = Path(__file__).resolve().parents[2] / "scripts" / "agent-private-guard"
GUARD_TIMEOUT_S = 120


class SourceError(RuntimeError):
    pass


class PrivateProjectRefused(SystemExit):
    """agent-private-guard refused the bearer: the command stops with exit 2. A SystemExit, so no handler of a
    failed source (`except SourceError`, `except Exception`) reads the refusal as a source to skip. The server's
    threads, where a SystemExit would end the thread for good, catch it by name. `str()` is why, not the code."""

    def __init__(self, why: str) -> None:
        super().__init__(2)
        self.why = why

    def __str__(self) -> str:
        return self.why


def private_guard(server: str, token: object) -> None:
    """Return when TOKEN may be sent to the Dispatch at SERVER; else say why and stop with exit 2."""
    try:
        # This interpreter runs the guard (standard library only), whatever PATH holds. TOKEN is envoy.json's JSON
        # value, sent as `Bearer {token}`: str() hands the guard those same characters for any JSON type, where a
        # number or list would raise past the handler below, and input=None would hand it this process's stdin.
        run = subprocess.run([sys.executable, str(GUARD), server], input=str(token), capture_output=True, text=True,
                             timeout=GUARD_TIMEOUT_S)
        why = (run.stderr.strip() or f"agent-private-guard exited {run.returncode}") if run.returncode else ""
    except (OSError, subprocess.SubprocessError) as e:
        why = f"{GUARD} could not run ({e})"
    if why:
        print(f"chief-dashboard: {why.rstrip('.')}; Dispatch is not read", file=sys.stderr)
        raise PrivateProjectRefused(why)


def _config() -> dict:
    return json.loads(ENVOY_CONFIG.read_text())


def _open(req: urllib.request.Request, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode(errors="replace")
        raise SourceError(f"{req.get_method()} {req.full_url}: HTTP {e.code}: {body}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError(f"{req.get_method()} {req.full_url}: {e}") from e


class Dispatch:
    def __init__(self, timeout: float = 30) -> None:
        cfg = _config()["dispatch"]
        self.server = cfg["serverUrl"].rstrip("/")
        self._token = cfg["token"]
        self.timeout = timeout

    def get(self, path: str):
        private_guard(self.server, self._token)
        req = urllib.request.Request(f"{self.server}{path}", headers={"Authorization": f"Bearer {self._token}"})
        return json.loads(_open(req, self.timeout))

    def open(self, path: str, timeout: float = 600):
        """The raw response, to stream a large file; the caller closes it (use it as a context manager)."""
        private_guard(self.server, self._token)
        req = urllib.request.Request(f"{self.server}{path}", headers={"Authorization": f"Bearer {self._token}"})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise SourceError(f"GET {req.full_url}: HTTP {e.code}: {e.read()[:300].decode(errors='replace')}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SourceError(f"GET {req.full_url}: {e}") from e

    def projects(self) -> list[dict]:
        return self.get("/api/v1/projects")

    def issue(self, key: str) -> dict:
        return self.get(f"/api/v1/issues/{urllib.parse.quote(key)}")

    def stream_issues(self) -> list[dict]:
        """Every open issue labelled `stream`, in any project."""
        return self.get("/api/v1/issues?label=stream&open=true")

    def file_version(self, key: str, slug: str, number: int):
        """One version of an uploaded file, parsed as JSON."""
        return self.get(f"/api/v1/issues/{urllib.parse.quote(key)}/artifacts/{slug}/versions/{number}")

    def open_asks(self, project: str) -> list[dict]:
        return self.get(f"/api/v1/asks/open?project={urllib.parse.quote(project)}")["asks"]

    def agents(self) -> list[dict]:
        return self.get("/api/v1/agents")


def envoy_send(session_id: str, message: str, idempotency_key: str, timeout: float = 20,
               source: str = "human") -> dict:
    """Send `message` to `session_id`; the hub's envelope. Its `recipient` is set when the hub routed it to a live
    subscriber of that session. A retry with the same key is one message. `source` is who the message is from:
    `human` is Sami himself, which agents act on as his own words, and `agent` is this program (the dispatch-check
    re-drive, which carries his answer but is not him)."""
    hub = _config()["envoy"]["url"].rstrip("/")
    body = {
        "source": source,
        "target_session": session_id,
        "message": message,
        "idempotency_key": idempotency_key,
        "urgency": "med",
        "expects_reply": "optional",
    }
    req = urllib.request.Request(
        f"{hub}/v1/messages/send",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    return json.loads(_open(req, timeout))


def envoy_sessions(timeout: float = 20) -> list[dict]:
    """Every session the hub knows, with its `topics`. Used to tell who already receives a Dispatch issue's
    messages by subscription, so the standup room does not hand the same line to a lead twice."""
    hub = _config()["envoy"]["url"].rstrip("/")
    req = urllib.request.Request(f"{hub}/v1/sessions", method="GET")
    got = json.loads(_open(req, timeout))
    return got if isinstance(got, list) else got.get("sessions") or []


def envoy_role(role: str, timeout: float = 20) -> dict | None:
    """The hub's record of ROLE (`holder` is the session that holds it, `title` that session's title), or None when
    nobody holds it (the hub answers 404). The re-drive names a Legion controller by it."""
    hub = _config()["envoy"]["url"].rstrip("/")
    req = urllib.request.Request(f"{hub}/v1/roles/{urllib.parse.quote(role, safe='')}", method="GET")
    try:
        got = json.loads(_open(req, timeout))
    except SourceError as e:
        if isinstance(e.__cause__, urllib.error.HTTPError) and e.__cause__.code == 404:
            return None
        raise
    return got if isinstance(got, dict) and got.get("holder") else None
