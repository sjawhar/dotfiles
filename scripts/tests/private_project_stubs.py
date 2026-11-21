"""Stand-ins for the services scripts/agent-private-check reads, for its tests and the tests of the
readers that run scripts/agent-private-guard.

FlagObject: the private-projects flag object as Cloud Storage serves it with `?alt=media`. `body`
None answers 404, as a bucket or object that does not exist does. Each request's Authorization
header is recorded in `auth`, so a test can name the credential the flag was read with.

DispatchStub: Dispatch's GET /api/v1/whoami for the bearers in `answers` (bearer -> JSON body, in
whoami's agent shape: `projects` and `private_projects`), 401 for any other bearer, and `routes`
(path with query -> JSON body) for every other GET or POST; anything else is 404. `down` answers 503
to everything, as a Dispatch that cannot answer does. Every request is recorded in `calls` as
(method, path with query, Authorization header).

FederationStub: an Azure-style instance token endpoint and Google STS's token exchange, the first
two steps of the external_account credential installers/gcloud-box.sh writes off Google Cloud;
iam_credentials_stub.py serves the third.

Each binds 127.0.0.1 on an ephemeral port; `.url` names the server, or the object (FlagObject).
Tokens and bodies are made-up values; nothing here is a credential.
"""

from __future__ import annotations

import http.server
import json
import threading
from typing import Any

FLAG_ON: dict[str, Any] = {"enabled": True, "enabled_since": "2026-11-20T00:00:00Z"}
FLAG_OFF: dict[str, Any] = {"enabled": False}
PRIV_TOKEN = "dsp_test_lists_priv_0123456789abcdef"
PUB_TOKEN = "dsp_test_lists_pub_0123456789abcdef"
WHOAMI_PRIV = {"kind": "agent", "projects": ["PRIV", "PUB"], "private_projects": ["PRIV"]}
WHOAMI_PUB = {"kind": "agent", "projects": ["PUB"]}
WHOAMI = "/api/v1/whoami"


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, handler: type[http.server.BaseHTTPRequestHandler]) -> None:
        super().__init__(("127.0.0.1", 0), handler)
        self.lock = threading.Lock()
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server's name
        pass

    def reply(self, status: int, body: Any = None) -> None:
        """BODY as JSON; a str is sent as it is (a flag that is not JSON), None as an empty body."""
        data = body.encode() if isinstance(body, str) else b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FlagObject(_Server):
    PATH = "/storage/v1/b/flags-bucket/o/flags%2Fprivate-projects.json"

    def __init__(self, body: dict[str, Any] | None = None) -> None:
        self.body: dict[str, Any] | str | None = body
        self.auth: list[str] = []
        super().__init__(_FlagHandler)

    @property
    def url(self) -> str:
        return f"{self.base}{self.PATH}?alt=media"


class _FlagHandler(_Handler):
    server: FlagObject

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        with self.server.lock:
            self.server.auth.append(self.headers.get("Authorization", ""))
            body = self.server.body
        if body is None or self.path != f"{FlagObject.PATH}?alt=media":
            self.reply(404, {"error": {"code": 404, "message": "No such object"}})
        else:
            self.reply(200, body)


class DispatchStub(_Server):
    def __init__(self, answers: dict[str, dict[str, Any]] | None = None, routes: dict[str, Any] | None = None) -> None:
        self.answers: dict[str, dict[str, Any]] = dict(answers or {})
        self.routes: dict[str, Any] = dict(routes or {})
        self.down = False
        self.calls: list[tuple[str, str, str]] = []
        super().__init__(_DispatchHandler)

    @property
    def url(self) -> str:
        return self.base

    def paths(self, method: str | None = None) -> list[str]:
        """The path of every request, whoami's included, in order (only METHOD's when given)."""
        with self.lock:
            return [p for m, p, _ in self.calls if method in (None, m)]

    def others(self) -> list[str]:
        """Every request but whoami: what the reader itself asked for."""
        return [p for p in self.paths() if p != WHOAMI]


class _DispatchHandler(_Handler):
    server: DispatchStub

    def answer(self, method: str) -> None:
        if method == "POST":
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
        auth = self.headers.get("Authorization", "")
        with self.server.lock:
            self.server.calls.append((method, self.path, auth))
            down, answers, routes = self.server.down, dict(self.server.answers), dict(self.server.routes)
        if down:
            self.reply(503, {"error": "unavailable"})
        elif self.path == WHOAMI:
            bearer = auth.removeprefix("Bearer ")
            if bearer in answers:
                self.reply(200, answers[bearer])
            else:
                self.reply(401, {"error": "unauthorized"})
        elif self.path in routes:
            self.reply(200, routes[self.path])
        else:
            self.reply(404, {"error": "not found"})

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        self.answer("GET")

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        self.answer("POST")


class FederationStub(_Server):
    """An Azure-style instance token endpoint (GET /imds/token, which wants `Metadata: True`) and
    Google STS's token exchange (POST /v1/token), the first two steps of an external_account
    credential such as installers/gcloud-box.sh writes. Each request is recorded in `calls` as
    (method, path, the Metadata header, the form body)."""

    SUBJECT = "stub-azure-subject"
    FEDERATED = "stub-federated-token"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []
        super().__init__(_FederationHandler)

    def credential(self, impersonation_url: str) -> dict[str, Any]:
        """An external_account configuration that uses this stand-in, then IMPERSONATION_URL."""
        return {
            "type": "external_account",
            "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/azure/providers/entra",
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "token_url": f"{self.base}/v1/token",
            "service_account_impersonation_url": impersonation_url,
            "credential_source": {
                "url": f"{self.base}/imds/token",
                "headers": {"Metadata": "True"},
                "format": {"type": "json", "subject_token_field_name": "access_token"},
            },
        }


class _FederationHandler(_Handler):
    server: FederationStub

    def record(self, method: str, body: str) -> None:
        with self.server.lock:
            self.server.calls.append((method, self.path, self.headers.get("Metadata", ""), body))

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        self.record("GET", "")
        if self.path == "/imds/token" and self.headers.get("Metadata") == "True":
            self.reply(200, {"access_token": FederationStub.SUBJECT})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        self.record("POST", body)
        if self.path == "/v1/token":
            self.reply(200, {"access_token": FederationStub.FEDERATED, "token_type": "Bearer", "expires_in": 3600,
                             "issued_token_type": "urn:ietf:params:oauth:token-type:access_token"})
        else:
            self.reply(404, {"error": "not found"})


def flag_env(flag: FlagObject, metadata_host: str, cache: str) -> dict[str, str]:
    """The variables that point the check at FLAG, read with the token of the metadata server at
    METADATA_HOST (gce_metadata_stub.py, or a closed port for "no metadata server"), and keep its
    copies under CACHE."""
    return {"AGENT_PRIVATE_FLAG_URL": flag.url, "GCE_METADATA_HOST": metadata_host, "XDG_CACHE_HOME": cache}
