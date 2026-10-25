#!/usr/bin/env python3
"""Extract Sami's Dispatch events (comments, ask answers, messages) since a timestamp.

Ask answers carry free text and spec comments are where design corrections happen; the
transcript extractor never sees either. Reads every issue of every project, keeps issues
updated at or after --since, pages each one's events, and keeps events by the actor whose id
starts with --actor. Writes JSONL (one event per line, the input of classify-sami-events.py)
and a readable transcript in time order with the ask question beside each answer. D<n> in the
transcript is the event's 1-based position in created_at order, the classifier's DN.

Dispatch's URL and token come from the envoy.json a command reads, resolved as Legion's
envoy-client resolves them for a script (envoyConfigPath("command"), dispatchSettings): the file
ENVOY_CONFIG_FILE names, else a named omp profile's $PI_CODING_AGENT_DIR/envoy.json (OMP_PROFILE
set and not default), else, under Claude Code (CLAUDE_CODE_CHILD_SESSION=1, which Claude Code sets
in every command it runs), $CLAUDE_CONFIG_DIR/envoy.json, else $OPENCODE_CONFIG_DIR/envoy.json,
else ${XDG_CONFIG_HOME:-~/.config}/legion/envoy.json; each must be an absolute path, and an empty
variable counts as unset. A checkout's .envoy/envoy.json is not read. A file that sets
dispatch.enabled to false, or that is not a valid envoy.json, is refused. DISPATCH_URL overrides
the server, which is otherwise the enabled section's dispatch.serverUrl (default
http://localhost:8766). The token is DISPATCH_TOKEN_FILE's trimmed contents, else DISPATCH_TOKEN,
else dispatch.token, which goes only to the server written beside it: with DISPATCH_URL naming
another origin, the script refuses. A dispatch.token starting with `!` is a command, run under
/bin/sh -c for at most 10 s, whose trimmed stdout is the token; neither of its streams is printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

KEEP = {"comment.created", "ask.answered", "message.created", "comment.edited", "ask.created"}
# The API root and bearer token, resolved once by main() through dispatch_settings().
BASE_URL = ""
TOKEN = ""
# The server when neither DISPATCH_URL nor dispatch.serverUrl names one, and how long a `!`
# dispatch.token may run: envoy-client's DEFAULT_SERVER_URL and its command limit.
DEFAULT_SERVER_URL = "http://localhost:8766"
COMMAND_TIMEOUT_S = 10
# Whitespace or a control character, which an Authorization header cannot carry.
UNSENDABLE = re.compile(r"[\s\x00-\x1f\x7f]")
# envoy-client's schema for each section: every key it knows and the kind of value it takes.
SECTIONS = {
    "dispatch": {"enabled": "boolean", "serverUrl": "http", "token": "string"},
    "envoy": {"enabled": "boolean", "url": "http", "natsUrls": "strings", "token": "string", "natsSeed": "string"},
}
KIND_PROBLEMS = {
    "boolean": "expected a boolean",
    "string": "expected a string",
    "strings": "expected an array of strings",
    "http": "must be an http or https URL",
}


def variable(env: dict[str, str], name: str) -> str | None:
    """A variable's value, with an empty one read as unset."""
    return env.get(name) or None


def absolute(name: str, value: str) -> str:
    if not os.path.isabs(value):
        raise ValueError(f"{name} is {value}, which is not an absolute path")
    return value


def envoy_config_path(env: dict[str, str]) -> Path:
    """The envoy.json a command reads (the order in the module docstring)."""
    handed = variable(env, "ENVOY_CONFIG_FILE")
    if handed:
        return Path(absolute("ENVOY_CONFIG_FILE", handed))
    profile = variable(env, "OMP_PROFILE")
    claude_code = env.get("CLAUDE_CODE_CHILD_SESSION") == "1"
    directories = (
        ("PI_CODING_AGENT_DIR", variable(env, "PI_CODING_AGENT_DIR") if profile not in (None, "default") else None),
        ("CLAUDE_CONFIG_DIR", variable(env, "CLAUDE_CONFIG_DIR") if claude_code else None),
        ("OPENCODE_CONFIG_DIR", variable(env, "OPENCODE_CONFIG_DIR")),
    )
    for name, directory in directories:
        if directory:
            return Path(absolute(name, directory)) / "envoy.json"
    xdg = variable(env, "XDG_CONFIG_HOME")
    if xdg:
        return Path(absolute("XDG_CONFIG_HOME", xdg)) / "legion" / "envoy.json"
    return Path(variable(env, "HOME") or Path.home()) / ".config" / "legion" / "envoy.json"


def http_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = urllib.parse.urlsplit(value)
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


def fits(value: object, kind: str) -> bool:
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "string":
        return isinstance(value, str)
    if kind == "strings":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return http_url(value)


def file_problem(data: Any) -> str | None:
    """The first thing envoy-client's schema refuses in a parsed envoy.json, or None."""
    if not isinstance(data, dict):
        return "expected a JSON object"
    if "natsUrls" in data:
        return "the top-level natsUrls is not read; move it to envoy.natsUrls"
    if "$schema" in data and not isinstance(data["$schema"], str):
        return "$schema: expected a string"
    for name, shape in SECTIONS.items():
        if name not in data:
            continue
        section = data[name]
        if not isinstance(section, dict):
            return f"{name}: expected an object"
        extra = sorted(key for key in section if key not in shape)
        if extra:
            return f"unrecognized {name} key(s): {', '.join(f'{name}.{key}' for key in extra)}"
        for key, kind in shape.items():
            if key in section and not fits(section[key], kind):
                return f"{name}.{key}: {KIND_PROBLEMS[kind]}"
    return None


def dispatch_section(path: Path) -> dict[str, Any] | None:
    """The file's dispatch section ({} when it has none), None when there is no file, or
    ValueError naming the file and what is wrong with it, never the file's text."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, UnicodeDecodeError) as error:
        reason = error.strerror if isinstance(error, OSError) else "not UTF-8"
        raise ValueError(f"{path}: could not be read ({reason})") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid JSON (line {error.lineno} column {error.colno})") from None
    problem = file_problem(data)
    if problem:
        raise ValueError(f"{path}: {problem}")
    return data.get("dispatch", {})


def origin(url: str) -> str:
    """scheme://host[:port] of an http(s) URL, lowercased, without user info or the scheme's
    default port: the origin envoy-client pairs a file's token with (`new URL(url).origin`)."""
    parts = urllib.parse.urlsplit(url)
    scheme, host, port = parts.scheme.lower(), parts.hostname or "", parts.port
    if ":" in host:
        host = f"[{host}]"
    default = {"http": 80, "https": 443}.get(scheme)
    return f"{scheme}://{host}" + ("" if port in (None, default) else f":{port}")


def trimmed(value: str) -> str:
    return value.strip(" \t\n\r\f\v")


def secret_file_value(name: str, path: str) -> str:
    """The trimmed contents of the file a <NAME> pointer names, or ValueError naming NAME and the
    path, never the contents."""
    try:
        value = trimmed(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        raise ValueError(f"{name} names {path}, which could not be read") from None
    if not value:
        raise ValueError(f"{name} names {path}, which is empty")
    if UNSENDABLE.search(value):
        raise ValueError(f"{name} names {path}, which holds whitespace or a control character")
    return value


def command_value(command: str, field: str, path: Path, env: dict[str, str]) -> str:
    """The value of a `!` field: COMMAND under /bin/sh -c in a session of its own, stdin from
    /dev/null, stdout to a private file (so a background process holding it open cannot hold this
    script up once the command exits), for at most COMMAND_TIMEOUT_S seconds, after which its whole
    process group is killed. Its exit status decides; its trimmed stdout is the value. Neither
    stream is ever shown: a failure names the field and the file only."""
    with tempfile.TemporaryFile() as out:
        try:
            process = subprocess.Popen(
                ["/bin/sh", "-c", command],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        except OSError as error:
            raise ValueError(f"{path}: {field}'s command could not start ({error.strerror})") from None
        try:
            status = process.wait(timeout=COMMAND_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise ValueError(f"{path}: {field}'s command did not finish within {COMMAND_TIMEOUT_S} s") from None
        if status != 0:
            how = signal.Signals(-status).name if status < 0 else f"status {status}"
            raise ValueError(f"{path}: {field}'s command exited with {how}")
        out.seek(0)
        value = trimmed(out.read().decode("utf-8", errors="replace"))
    if not value:
        raise ValueError(f"{path}: {field}'s command printed nothing")
    if UNSENDABLE.search(value):
        raise ValueError(f"{path}: {field}'s command printed more than one line, or whitespace inside its value")
    return value


def dispatch_settings(env: dict[str, str]) -> tuple[str, str]:
    """The API root and bearer token, or ValueError naming what is missing or refused."""
    path = envoy_config_path(env)
    section = dispatch_section(path)
    named_url = variable(env, "DISPATCH_URL")
    own = section or {}
    if own.get("enabled") is False:
        raise ValueError(f"{path} sets dispatch.enabled to false: Dispatch is off")
    if named_url:
        if not http_url(named_url):
            raise ValueError("DISPATCH_URL must be an http or https URL")
        url = named_url
    elif own.get("enabled") is True:
        url = own.get("serverUrl") or DEFAULT_SERVER_URL
    elif section is None:
        raise ValueError(f"no envoy.json at {path}, and DISPATCH_URL is not set: Dispatch is off")
    else:
        raise ValueError(f"{path} has no dispatch section with enabled true, and DISPATCH_URL is not set: Dispatch is off")
    pointer, plain = variable(env, "DISPATCH_TOKEN_FILE"), variable(env, "DISPATCH_TOKEN")
    if pointer:
        token = secret_file_value("DISPATCH_TOKEN_FILE", pointer)
    elif plain:
        if UNSENDABLE.search(plain):
            raise ValueError("DISPATCH_TOKEN holds whitespace or a control character")
        token = plain
    elif "token" in own:
        server = own.get("serverUrl") or DEFAULT_SERVER_URL
        if named_url and origin(named_url) != origin(server):
            raise ValueError(
                f"DISPATCH_URL names {origin(named_url)}, but {path}'s dispatch.token is for {origin(server)}; "
                "set DISPATCH_TOKEN or DISPATCH_TOKEN_FILE for it"
            )
        token = own["token"]
        if token.startswith("!"):
            token = command_value(token[1:], "dispatch.token", path, env)
        elif UNSENDABLE.search(token):
            raise ValueError(f"{path}: dispatch.token holds whitespace or a control character")
        elif not token:
            raise ValueError(f"{path}: dispatch.token must be a non-empty bearer token")
    else:
        raise ValueError(f"no Dispatch token: set DISPATCH_TOKEN or DISPATCH_TOKEN_FILE, or dispatch.token in {path}")
    return url.rstrip("/") + "/api/v1", token


def get(path: str):
    req = urllib.request.Request(f"{BASE_URL}{path}", headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def projects() -> list[str]:
    data = get("/projects")
    items = data.get("projects", data) if isinstance(data, dict) else data
    return [p["key"] for p in items]


def issues(project: str, since: str) -> list[str]:
    keys, offset = [], 0
    while True:
        page = get(f"/issues?project={project}&limit=250&offset={offset}")
        rows = page["issues"]
        keys += [r["key"] for r in rows if (r.get("updated_at") or "") >= since]
        offset += len(rows)
        if not rows or offset >= page.get("total", 0):
            return keys


def events(key: str) -> list[dict]:
    out, cursor = [], None
    while True:
        q = "limit=200" + ("" if cursor is None else f"&after={cursor}")
        page = get(f"/issues/{key}/events?{q}")
        out += page
        if len(page) < 200:
            return out
        cursor = page[-1]["seq"]


def render(e: dict) -> str:
    p = e.get("payload") or {}
    if e["type"] == "ask.answered":
        a = p.get("answer") or {}
        q = p.get("question") or ""
        picked = ", ".join(a.get("selected") or [])
        return f"ASK: {q}\nPICKED: {picked}\nTEXT: {a.get('text') or ''}"
    body = p.get("body") or p.get("text") or ""
    quote = (p.get("anchor") or {}).get("quote") if isinstance(p.get("anchor"), dict) else None
    ask_q = p.get("ask_question")
    head = f"ON ASK: {ask_q}\n" if ask_q else (f"ON QUOTE: {quote}\n" if quote else "")
    return head + body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", required=True, help="ISO timestamp, e.g. 2026-09-20T17:00:00Z")
    ap.add_argument("--out", required=True, help="JSONL output")
    ap.add_argument("--text", required=True, help="readable transcript output")
    ap.add_argument("--actor", default="sami@", help="keep events whose actor id starts with this")
    args = ap.parse_args()
    global BASE_URL, TOKEN
    try:
        BASE_URL, TOKEN = dispatch_settings(dict(os.environ))
    except ValueError as error:
        sys.exit(f"extract-dispatch-human: {error}")

    keys: list[str] = []
    for project in projects():
        keys += issues(project, args.since)
    with ThreadPoolExecutor(16) as pool:
        all_events = [e for evs in pool.map(events, keys) for e in evs]
    human = sorted(
        (e for e in all_events
         if (e.get("actor") or {}).get("kind") == "user"
         and (e.get("actor") or {}).get("id", "").startswith(args.actor)
         and e["type"] in KEEP and e["created_at"] >= args.since),
        key=lambda e: e["created_at"],
    )
    with open(args.out, "w") as f:
        for e in human:
            f.write(json.dumps(e) + "\n")
    with open(args.text, "w") as f:
        for n, e in enumerate(human, 1):
            f.write(f"--- D{n} {e['created_at'][:16]} {e['issue_key']} {e['type']}\n{render(e)}\n\n")
    print(f"{len(keys)} issues updated since {args.since}; {len(all_events)} events; {len(human)} kept")


if __name__ == "__main__":
    main()
