"""The one Envoy listener client the Python watches share.

Eight watches each carried a copy of this class -- four variants, 619 lines, every one
documented as resolving the listener "the way scripts/envoy resolves it". This is that one
copy. `scripts/envoy` stays the command-line client and is unchanged: it is what a shell, and
a script that sends one message and exits, should still use. This is for a watch that already
speaks HTTP in-process and would otherwise fork a process per message.

Resolution follows `scripts/envoy`, which follows Legion's envoy-client: the `envoy` section of
the envoy.json at ENVOY_CONFIG_FILE, else ${XDG_CONFIG_HOME:-~/.config}/legion/envoy.json.
ENVOY_URL and ENVOY_TOKEN override that section's `url` and `token`. A token starting with `!`
is a command, run through /bin/sh for at most 10 s with neither of its streams ever printed and
its stdout taken as the token; a command that fails leaves no token rather than a wrong one.
`"enabled": false` raises SystemExit, so a caller never sends to a listener the file disowns.

This resolves less than `scripts/envoy` does: no named-profile `PI_CODING_AGENT_DIR`, no Claude
Code `CLAUDE_CONFIG_DIR`, no `OPENCODE_CONFIG_DIR`, and no ENVOY_TOKEN_FILE. Every caller is a
systemd-driven watch with none of those set, and that is exactly what each copy of the class
already did; a caller that needs the full rule should run `scripts/envoy`.

A caller gives its own logger, since each watch writes its progress its own way:

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import envoy_client

    class Envoy(envoy_client.Envoy):
        def __init__(self, timeout: float = 8.0) -> None:
            super().__init__(timeout=timeout, log=log)

`log` is called with one string whenever a send or publish did not answer 200; nothing else
here prints. `log=None` says nothing.

Tests: python3 -m pytest scripts/tests/test_envoy_client.py
"""

import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path


DEFAULT_ENVOY_URL = "http://127.0.0.1:9020"
TOKEN_COMMAND_TIMEOUT = 10.0


class Envoy:
    """The listener this machine's sessions use, resolved the way scripts/envoy resolves it."""

    def __init__(self, timeout: float = 8.0, log: Callable[[str], None] | None = None) -> None:
        self.log: Callable[[str], None] = log or (lambda _message: None)
        path = os.environ.get("ENVOY_CONFIG_FILE") or str(
            Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "legion/envoy.json"
        )
        config: dict = {}
        try:
            config = json.loads(Path(path).read_text()).get("envoy") or {}
        except (OSError, ValueError):
            config = {}
        if config.get("enabled") is False:
            raise SystemExit(f"{path} sets envoy.enabled to false: Envoy is off")
        self.url = (os.environ.get("ENVOY_URL") or config.get("url") or DEFAULT_ENVOY_URL).rstrip("/")
        self.timeout = timeout
        self.token = bearer(os.environ.get("ENVOY_TOKEN") or config.get("token") or "")

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict | list]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as answer:
                raw = answer.read().decode() or "{}"
                return answer.status, json.loads(raw)
        except urllib.error.HTTPError as error:
            raw = error.read().decode() or "{}"
            try:
                return error.code, json.loads(raw)
            except ValueError:
                return error.code, {"error": raw.strip()}
        except (TimeoutError, urllib.error.URLError, ValueError, OSError) as error:
            return 0, {"error": str(error)}

    def send(self, session_id: str, message: str) -> bool:
        status, body = self.request(
            "POST",
            "/v1/messages/send",
            {"target_session": session_id, "message": message, "source": "envoy"},
        )
        if status != 200:
            self.log(f"  send to {session_id} failed: {status} {body}")
        return status == 200

    def publish(self, topic: str, message: str) -> bool:
        status, body = self.request(
            "POST",
            "/v1/messages/publish",
            {"topic": topic, "message": message, "source": "envoy"},
        )
        if status != 200:
            self.log(f"  publish to {topic} failed: {status} {body}")
        return status == 200


def bearer(token: str) -> str:
    """The bearer an envoy.json `token` or ENVOY_TOKEN names, running a `!` command for it.

    Neither of the command's streams is ever printed: the token is on one of them. A command
    that fails, times out or cannot start leaves no token, so the request goes out unauthorized
    and Envoy refuses it, rather than carrying a wrong one.
    """
    if not token.startswith("!"):
        return token
    try:
        done = subprocess.run(
            ["/bin/sh", "-c", token[1:]],
            capture_output=True,
            text=True,
            timeout=TOKEN_COMMAND_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""
