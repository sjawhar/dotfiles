"""The page's two outside services, read with the machine's Envoy config (~/.config/legion/envoy.json):
Dispatch's REST API (issues, scoreboard files, open asks, live sessions) and the Envoy hub (a note to a lead)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENVOY_CONFIG = Path(os.environ.get("ENVOY_CONFIG_FILE", Path.home() / ".config" / "legion" / "envoy.json"))


class SourceError(RuntimeError):
    pass


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
        req = urllib.request.Request(f"{self.server}{path}", headers={"Authorization": f"Bearer {self._token}"})
        return json.loads(_open(req, self.timeout))

    def open(self, path: str, timeout: float = 600):
        """The raw response, to stream a large file; the caller closes it (use it as a context manager)."""
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
