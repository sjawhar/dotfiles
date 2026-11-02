#!/usr/bin/env python3
"""A stand-in for the Compute Engine metadata server, for tests of the Google-VM branches.

It answers the paths gcloud (googlecloudsdk/core/credentials/gce_read.py, gce.py, gce_cache.py),
google-auth (google/auth/compute_engine/_metadata.py, credentials.py) and this repository's scripts
read, only with the `Metadata-Flavor: Google` request header, and puts that header on every answer
as the real server does (google-auth's ping checks it). Tokens are fixed strings shaped like the
real ones; nothing here is a credential. Each request path is recorded in `requests`.

As a library: `server = MetadataStub.start(account)`, then point GCE_METADATA_HOST (scripts,
google-auth, Go), GCE_METADATA_ROOT (gcloud) and GCE_METADATA_IP (google-auth's probe) at
`server.host`. As a command, for a manual end-to-end run: `gce_metadata_stub.py PORT [ACCOUNT]`.
"""

from __future__ import annotations

import http.server
import json
import sys
import threading
import urllib.parse

ACCOUNT = "dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com"
PROJECT = "theorem-agent-infra"
NUMERIC_PROJECT = "805240912354"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/userinfo.email"]
STUB_BEARER = "stub-metadata-bearer"


def id_token(audience: str) -> str:
    """A JWT-shaped string that names its audience, so a test can tell which one was asked for."""
    return "stubheader." + urllib.parse.quote(audience, safe="") + ".stubsignature"


class MetadataStub(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, account: str, bind: str = "127.0.0.1") -> None:
        super().__init__((bind, port), _Handler)
        self.account = account
        self.requests: list[str] = []

    @property
    def host(self) -> str:
        return f"{self.server_address[0]}:{self.server_address[1]}"

    @classmethod
    def start(cls, account: str = ACCOUNT, bind: str = "127.0.0.1") -> "MetadataStub":
        server = cls(0, account, bind)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def stop(self) -> None:
        self.shutdown()
        self.server_close()

    def answer(self, path: str, query: dict[str, list[str]]) -> tuple[int, str, str]:
        """(status, content type, body) for one GET."""
        text = "application/text"
        # google-auth's ping (compute_engine/_metadata.py) GETs the bare root and wants a 200.
        if path in ("/", "/computeMetadata/"):
            return 200, text, "computeMetadata/\n"
        prefix = "/computeMetadata/v1"
        if not path.startswith(prefix):
            return 404, text, "not found"
        path = path[len(prefix):]
        accounts = ("default", self.account)
        if path == "/instance/id":
            return 200, text, "1234567890123456789"
        if path == "/project/project-id":
            return 200, text, PROJECT
        if path == "/project/numeric-project-id":
            return 200, text, NUMERIC_PROJECT
        if path == "/universe/universe-domain":
            return 200, text, "googleapis.com"
        if path == "/instance/zone":
            return 200, text, f"projects/{NUMERIC_PROJECT}/zones/us-west4-a"
        if path == "/instance/service-accounts/":
            return 200, text, f"default/\n{self.account}/\n"
        parts = path.split("/")  # ['', 'instance', 'service-accounts', who, leaf]
        if len(parts) == 5 and parts[1:3] == ["instance", "service-accounts"] and parts[3] in accounts:
            leaf = parts[4]
            if leaf == "" and query.get("recursive") == ["true"]:
                info = {"aliases": ["default"], "email": self.account, "scopes": SCOPES}
                return 200, "application/json", json.dumps(info)
            if leaf == "email":
                return 200, text, self.account
            if leaf == "scopes":
                return 200, text, "\n".join(SCOPES)
            if leaf == "token":
                token = {"access_token": STUB_BEARER, "expires_in": 3599, "token_type": "Bearer"}
                return 200, "application/json", json.dumps(token)
            if leaf == "identity" and query.get("audience"):
                return 200, text, id_token(query["audience"][0])
        return 404, text, "not found"


class _Handler(http.server.BaseHTTPRequestHandler):
    server: MetadataStub

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        url = urllib.parse.urlsplit(self.path)
        self.server.requests.append(self.path)
        if self.headers.get("Metadata-Flavor") != "Google":
            status, kind, body = 403, "text/plain", "Missing Metadata-Flavor: Google"
        else:
            status, kind, body = self.server.answer(url.path, urllib.parse.parse_qs(url.query))
        data = body.encode()
        self.send_response(status)
        self.send_header("Metadata-Flavor", "Google")
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: object) -> None:
        if __name__ == "__main__":
            sys.stderr.write(f"metadata-stub: {fmt % args}\n")


if __name__ == "__main__":
    stub = MetadataStub(int(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else ACCOUNT)
    print(f"metadata-stub: serving {stub.host} as {stub.account}", file=sys.stderr, flush=True)
    stub.serve_forever()
