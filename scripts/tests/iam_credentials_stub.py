"""A stand-in for Google's IAM Credentials API, for tests of a caller that mints another account's tokens.

It answers the two methods `projects/-/serviceAccounts/<email>:generateAccessToken` and
`:generateIdToken` in the shapes the real API returns (iamcredentials.googleapis.com/v1): an
`accessToken` with its `expireTime`, and a `token` that is a JWT-shaped string whose payload holds
`aud`, `email` and `exp`. Tokens are fixed strings shaped like the real ones; nothing here is a
credential. Each request is recorded in `calls` as (method, account, Authorization header, body).

As a library: `server = IamCredentialsStub.start()`, then point the caller's IAM Credentials base URL
at `server.url`. `server.refuse = True` makes every call answer 403 PERMISSION_DENIED, as the API
does for a caller without Token Creator; `server.access_lifetime` and `server.id_lifetime` set the
seconds until a minted access token and ID token expire.
"""

from __future__ import annotations

import base64
import datetime
import http.server
import json
import re
import threading
import time

PATH = re.compile(r"^/v1/projects/-/serviceAccounts/(?P<account>[^/:]+):(?P<method>generateAccessToken|generateIdToken)$")
REFUSAL: dict[str, object] = {
    "error": {
        "code": 403,
        "message": "Permission 'iam.serviceAccounts.getAccessToken' denied on resource (or it may not exist).",
        "status": "PERMISSION_DENIED",
    }
}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def id_token(account: str, audience: str, exp: int) -> str:
    """A JWT-shaped string whose payload names its account, audience and expiry."""
    payload = json.dumps({"aud": audience, "email": account, "exp": exp}).encode()
    return f"{_b64(b'{}')}.{_b64(payload)}.stubsignature"


class IamCredentialsStub(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, bind: str = "127.0.0.1") -> None:
        super().__init__((bind, 0), _Handler)
        self.calls: list[tuple[str, str, str, dict[str, object]]] = []
        self.refuse = False
        # A 200 whose token carries no expiry a caller can read: no expireTime, a token that is no JWT.
        self.no_expiry = False
        self.access_lifetime = 3600
        self.id_lifetime = 3600
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://{self.server_address[0]}:{self.server_address[1]}"

    @classmethod
    def start(cls) -> "IamCredentialsStub":
        server = cls()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def stop(self) -> None:
        self.shutdown()
        self.server_close()

    def count(self, method: str) -> int:
        return sum(1 for call in self.calls if call[0] == method)

    def answer(self, method: str, account: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
        with self._lock:
            n = len(self.calls)
        if self.refuse:
            return 403, REFUSAL
        now = int(time.time())
        if self.no_expiry:
            if method == "generateAccessToken":
                return 200, {"accessToken": f"stub-minted-access-{n}"}
            return 200, {"token": f"stub-minted-id-{n}"}
        if method == "generateAccessToken":
            expiry = datetime.datetime.fromtimestamp(now + self.access_lifetime, datetime.timezone.utc)
            return 200, {"accessToken": f"stub-minted-access-{n}", "expireTime": expiry.strftime("%Y-%m-%dT%H:%M:%SZ")}
        return 200, {"token": id_token(account, str(body.get("audience", "")), now + self.id_lifetime)}


class _Handler(http.server.BaseHTTPRequestHandler):
    @property
    def stub(self) -> IamCredentialsStub:
        # BaseRequestHandler declares `server` as a BaseServer; this handler serves only the stub.
        assert isinstance(self.server, IamCredentialsStub)
        return self.server

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        match = PATH.match(self.path)
        if not match:
            status, answer = 404, {"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}}
        else:
            method, account = match["method"], match["account"]
            status, answer = self.stub.answer(method, account, body)
            with self.stub._lock:
                self.stub.calls.append((method, account, self.headers.get("Authorization", ""), body))
        data = json.dumps(answer).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        pass
