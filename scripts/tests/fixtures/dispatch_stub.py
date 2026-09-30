#!/usr/bin/env python3
"""A fixture Dispatch for scripts/legion-brief-watch: its unit tests, and its W-b acceptance run.

Serves from --dir: `GET /api/v1/issues?…` answers issues.json; `GET /api/v1/issues/<key>/events`
answers the events in events/<key>.json with seq above `after`, at most `limit` (200 by default),
as Dispatch pages them. A bearer other than the contents of the file `token` gets 401, and while a
file named `fail` exists every request gets 500. A path Go's ServeMux would clean (`//api/…`) gets
its 301, which http.server would otherwise hide by collapsing the slashes. Each request's raw path
is appended to requests.log; headers are never logged, so the log cannot hold the token.
write_case() writes that layout.

    dispatch_stub.py --dir <fixture dir> [--port N]    # N=0 picks a free port; it prints the port
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def handler_for(root: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server's name)
            raw = self.requestline.split()[1]
            with (root / "requests.log").open("a", encoding="utf-8") as log:
                log.write(raw + "\n")
            url = urlsplit(self.path)
            if "//" in raw.split("?")[0]:
                return self.reply(301, {"code": "MOVED", "message": "Moved Permanently"})
            if (root / "fail").exists():
                return self.reply(500, {"code": "INTERNAL", "message": "fixture failure"})
            if self.headers.get("Authorization") != f"Bearer {(root / 'token').read_text().strip()}":
                return self.reply(401, {"code": "UNAUTHORIZED", "message": "bearer token required"})
            if url.path == "/api/v1/issues":
                return self.reply(200, json.loads((root / "issues.json").read_text()))
            parts = url.path.split("/")
            if len(parts) == 6 and parts[:4] == ["", "api", "v1", "issues"] and parts[5] == "events":
                events = root / "events" / f"{parts[4]}.json"
                if events.exists():
                    query = parse_qs(url.query)
                    after = int(query.get("after", ["0"])[0])
                    limit = int(query.get("limit", ["200"])[0])
                    return self.reply(200, [e for e in json.loads(events.read_text()) if e["seq"] > after][:limit])
            return self.reply(404, {"code": "NOT_FOUND", "message": url.path})

        def reply(self, status: int, body: object) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args: object) -> None:
            pass

    return Handler


def write_case(root: Path, token: str, issues: list[dict], events: dict[str, list[dict]]) -> None:
    """Writes a case for the server to serve: the bearer it accepts, the issue listing, and each
    listed issue's event log by key."""
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "token").write_text(token, encoding="utf-8")
    (root / "issues.json").write_text(json.dumps(issues), encoding="utf-8")
    for key, log in events.items():
        (root / "events" / f"{key}.json").write_text(json.dumps(log), encoding="utf-8")


def server(root: Path, port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler_for(root))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    httpd = server(args.dir, args.port)
    print(f"dispatch_stub: serving {args.dir} on http://127.0.0.1:{httpd.server_address[1]}", flush=True)
    httpd.serve_forever()
