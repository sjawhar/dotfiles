#!/usr/bin/env python3
"""
Inventory tool-call failures across Oh My Pi session transcripts (top-level
sessions AND nested subagent lanes) in a window.

Selection is by session-file mtime: every `*.jsonl` under the sessions dir
modified since the window start is read end to end, so long-lived sessions
contribute their whole history (same semantics as the 2026-09-16 bash audit
that this reproduces). Emits one JSONL row per FAILED tool call (session id,
timestamp, tool, command stem for bash, exit code, error excerpt, duration),
and with --summary prints per-tool totals plus the top-N bash command stems
by failure count and failure rate.

Usage:
    python extract-tool-errors.py [--days N | --since 'YYYY-MM-DD HH:MM']
                                  [--sessions-dir PATH] [--out OUT.jsonl]
                                  [--summary [N]]
"""

import argparse
import json
import os
import re
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def get_omp_sessions_dir() -> Path:
    return Path.home() / ".omp" / "agent" / "sessions"


SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|xox[pbc]-[A-Za-z0-9-]{8,}|gh[ps]_[A-Za-z0-9]{8,}"
    r"|AKIA[A-Z0-9]{12,}|Bearer [A-Za-z0-9._-]{12,})"
)


def redact(text: str) -> str:
    if not text:
        return text
    return SECRET_RE.sub("<redacted>", text)


# --------------------------------------------------------- command stemming
# Ported from the 2026-09-16 bash-invocation audit's final normalizer.

KNOWN_MULTIWORD = {
    "gh", "jj", "git", "docker", "pulumi", "kubectl", "npm", "npx", "uv",
    "aws", "tl", "pip", "pip3", "cargo", "go", "bun", "gcloud", "terraform",
    "helm", "az", "modal", "inspect", "docker-compose", "brew", "apt-get",
    "apt", "systemctl", "yarn", "pnpm", "poetry", "conda", "mise", "gws",
    "secrets", "hub", "knives",
}
_TOKEN_CLEAN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_JUNK_RE = re.compile(r"[(){}$;<>`\"'\[\]]")
_VALUE_FLAG_WORD_RE = re.compile(
    r"(directory|project|extra|group|package|dir$|path$|^file$|output|format|^name$|tag|branch|"
    r"host|port|user|profile|account|region|namespace|context|cwd|chdir|signal|kill-after|"
    r"config|module|target|url|repo|image|version|env-file|^env$|key|token|timeout|retries|"
    r"epochs|max-connections|sandbox|solver|deployment|python$)"
)


def _is_connector(t: str) -> bool:
    return t in ("&&", "||", ";", "|")


def _tokenize(cmd: str):
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        return cmd.split()


def _stem(command: str) -> str:
    if not command:
        return "<empty>"
    first_segment = re.split(r"[\n]", command, maxsplit=1)[0]
    tokens = _tokenize(first_segment)
    if not tokens:
        return "<empty>"
    n = len(tokens)
    i = 0

    def skip_generic_flags(i):
        while i < n:
            t = tokens[i]
            if _is_connector(t):
                i += 1
                continue
            if t.startswith("--") and "=" not in t:
                flagword = t[2:]
                i += 1
                if _VALUE_FLAG_WORD_RE.search(flagword) and i < n and not tokens[i].startswith("-"):
                    i += 1
                continue
            if re.match(r"^-[A-Za-z]$", t):
                i += 1
                if t in ("-u", "-C", "-S", "-p", "-s", "-k") and i < n and not tokens[i].startswith("-"):
                    i += 1
                continue
            if t.startswith("-") and "=" not in t:
                i += 1
                continue
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
                i += 1
                continue
            break
        return i

    changed = True
    guard = 0
    while changed and guard < 20:
        guard += 1
        changed = False
        if i >= n:
            break
        t = tokens[i]
        if t == "cd":
            j = i + 1
            if j < n and not _is_connector(tokens[j]):
                j += 1  # path token
            if j < n and _is_connector(tokens[j]):
                j += 1  # connector
                i = j
                changed = True
                continue
            break  # bare 'cd <path>' with nothing chained: 'cd' is the stem
        if t == "timeout":
            j = i + 1
            while j < n and tokens[j].startswith("-"):
                flag = tokens[j]
                j += 1
                if flag in ("-s", "--signal", "-k", "--kill-after") and j < n and not tokens[j].startswith("-"):
                    j += 1
            if j < n:
                j += 1  # duration
            i = j
            changed = True
            continue
        if t == "env":
            j = i + 1
            while j < n:
                tt = tokens[j]
                if tt in ("-i", "--ignore-environment"):
                    j += 1
                    continue
                if tt in ("-u", "--unset"):
                    j += 1
                    if j < n:
                        j += 1
                    continue
                if tt.startswith("--unset=") or (tt.startswith("-u") and len(tt) > 2):
                    j += 1
                    continue
                if tt in ("-C", "--chdir", "-S", "--split-string"):
                    j += 1
                    if j < n:
                        j += 1
                    continue
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tt):
                    j += 1
                    continue
                break
            i = j
            changed = True
            continue
        if t == "unset":
            j = i + 1
            while j < n:
                tok = tokens[j]
                if re.match(r"^[A-Za-z_][A-Za-z0-9_,]*$", tok):
                    j += 1
                    continue
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*;$", tok):
                    j += 1
                break
            if j < n and tokens[j] == ";":
                j += 1
            i = j
            changed = True
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            i += 1
            changed = True
            continue
        if t == "uv" and i + 1 < n and tokens[i + 1] == "run":
            i += 2
            i = skip_generic_flags(i)
            changed = True
            continue
        break

    i = skip_generic_flags(i)
    if i >= n:
        return "<flags-only>"
    prog = tokens[i]
    prog_base = prog.rsplit("/", 1)[-1]
    if prog_base in ("python", "python3") or re.match(r"^python3?\.\d+$", prog_base):
        j = i + 1
        while j < n and tokens[j].startswith("-") and tokens[j] not in ("-m", "-c"):
            j += 1
        if j < n and tokens[j] == "-m" and j + 1 < n:
            return f"python -m {tokens[j + 1]}"
        if j < n and tokens[j] == "-c":
            return "python -c"
        if j < n:
            return f"python {tokens[j].rsplit('/', 1)[-1]}"
        return "python"
    if prog_base in KNOWN_MULTIWORD:
        words = [prog_base]
        j = i + 1
        collected = 0
        while j < n and collected < 2:
            t2 = tokens[j]
            if _TOKEN_CLEAN_RE.match(t2):
                words.append(t2)
                collected += 1
                j += 1
            else:
                break
        return " ".join(words)
    return prog_base


def normalize_stem(command: str) -> str:
    stem = _stem(command)
    if stem in ("<empty>", "<flags-only>"):
        return stem
    if not stem or _JUNK_RE.search(stem):
        return "<complex-shell-expr>"
    return stem


# ------------------------------------------------------------- extraction

WALLTIME_RE = re.compile(r"Wall time:\s*([0-9.]+)\s*seconds")


def extract_text(content) -> str:
    if not isinstance(content, list):
        return ""
    return "\n".join(
        item.get("text", "")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    )


def is_failure(msg: dict, details: dict) -> bool:
    if msg.get("isError"):
        return True
    exit_code = details.get("exitCode")
    return exit_code is not None and exit_code != 0


def process_file(fp: str, rel: str, project: str, writer, stats):
    """One session transcript: match toolCall args to toolResult outcomes."""
    pending_bash = {}
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                # Cheap prefilter: only parse lines that can carry a bash call,
                # a bash result, or any failed result.
                is_call_line = '"toolCall"' in line and '"bash"' in line
                is_bash_result = '"toolName":"bash"' in line
                is_error_line = '"isError":true' in line
                if not (is_call_line or is_bash_result or is_error_line):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    stats["json_errors"] += 1
                    continue
                if obj.get("type") != "message":
                    continue
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                ts = obj.get("timestamp")
                if role == "assistant":
                    for item in msg.get("content") or []:
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "toolCall"
                            and item.get("name") == "bash"
                        ):
                            args = item.get("arguments") or {}
                            pending_bash[item.get("id")] = {
                                "command": args.get("command", ""),
                                "intent": args.get("i", ""),
                                "timestamp": ts,
                            }
                    continue
                if role != "toolResult":
                    continue
                tool = msg.get("toolName") or "<unknown>"
                details = msg.get("details") or {}
                failed = is_failure(msg, details)
                call = pending_bash.pop(msg.get("toolCallId"), None) if tool == "bash" else None

                stem = None
                if tool == "bash":
                    command = call["command"] if call else None
                    stem = normalize_stem(command) if command else "<no-call-matched>"
                    stats["bash_total"] += 1
                    stats["stem_totals"][stem] = stats["stem_totals"].get(stem, 0) + 1
                    if failed:
                        stats["bash_failed"] += 1
                        stats["stem_failures"][stem] = stats["stem_failures"].get(stem, 0) + 1
                if not failed:
                    continue
                stats["tool_failures"][tool] = stats["tool_failures"].get(tool, 0) + 1

                text = extract_text(msg.get("content"))
                wall_time_ms = details.get("wallTimeMs")
                if wall_time_ms is None:
                    m = WALLTIME_RE.search(text)
                    if m:
                        wall_time_ms = float(m.group(1)) * 1000.0
                if writer is not None:
                    row = {
                        "session_id": rel,
                        "project": project,
                        "timestamp": (call or {}).get("timestamp") or ts,
                        "tool": tool,
                        "command_stem": stem,
                        "command": redact((call or {}).get("command", ""))[:300] or None,
                        "intent": redact((call or {}).get("intent", ""))[:200] or None,
                        "exit_code": details.get("exitCode"),
                        "timed_out": bool(details.get("timedOut")),
                        "error_excerpt": redact(text)[:300],
                        "wall_time_ms": wall_time_ms,
                    }
                    writer.write(json.dumps(row) + "\n")
                stats["failure_rows"] += 1
            # bash calls that never got a result (session cut off / interrupted)
            for call in pending_bash.values():
                stem = normalize_stem(call["command"])
                stats["bash_total"] += 1
                stats["stem_totals"][stem] = stats["stem_totals"].get(stem, 0) + 1
    except OSError as e:
        print(f"  Error reading {fp}: {e}", file=sys.stderr)
        stats["file_errors"] += 1


def main():
    parser = argparse.ArgumentParser(
        description="Inventory failed tool calls across omp session transcripts"
    )
    parser.add_argument(
        "--days",
        type=float,
        default=7,
        help="Window: session files modified in the past N days (default: 7)",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Window start as ISO timestamp (e.g. '2026-09-05 22:48'); overrides --days",
    )
    parser.add_argument(
        "--sessions-dir",
        type=str,
        default=None,
        help="Sessions root (default: ~/.omp/agent/sessions)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="-",
        help="Failure-rows JSONL output path (default: stdout; ignored with --summary "
        "unless given explicitly)",
    )
    parser.add_argument(
        "--summary",
        nargs="?",
        const=30,
        type=int,
        default=None,
        metavar="N",
        help="Print per-tool totals and the top-N bash stems by failure count (default N: 30)",
    )
    args = parser.parse_args()

    sessions_dir = Path(args.sessions_dir) if args.sessions_dir else get_omp_sessions_dir()
    if not sessions_dir.is_dir():
        print(f"Error: sessions directory not found: {sessions_dir}", file=sys.stderr)
        sys.exit(1)

    if args.since:
        cutoff = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp()
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp()

    files = []
    for dirpath, _dirnames, filenames in os.walk(sessions_dir):
        for name in filenames:
            if not name.endswith(".jsonl"):
                continue
            fp = os.path.join(dirpath, name)
            try:
                if os.stat(fp).st_mtime >= cutoff:
                    files.append(fp)
            except OSError:
                continue
    files.sort()

    stats = {
        "bash_total": 0,
        "bash_failed": 0,
        "failure_rows": 0,
        "json_errors": 0,
        "file_errors": 0,
        "tool_failures": {},
        "stem_totals": {},
        "stem_failures": {},
    }

    writer = None
    out_file = None
    if args.summary is None or args.out != "-":
        if args.out == "-":
            writer = sys.stdout
        else:
            out_file = open(args.out, "w", encoding="utf-8")
            writer = out_file

    root = str(sessions_dir)
    for i, fp in enumerate(files, 1):
        rel = os.path.relpath(fp, root)
        project = rel.split(os.sep, 1)[0]
        process_file(fp, rel, project, writer, stats)
        if i % 1000 == 0:
            print(f"  {i}/{len(files)} files...", file=sys.stderr)

    if out_file is not None:
        out_file.close()

    print(
        f"window: file mtime >= {datetime.fromtimestamp(cutoff, tz=timezone.utc):%Y-%m-%d %H:%M} UTC"
        f" | {len(files)} session files | bash invocations: {stats['bash_total']}"
        f" ({stats['bash_failed']} failed) | failure rows: {stats['failure_rows']}"
        f" | json errors: {stats['json_errors']} | file errors: {stats['file_errors']}",
        file=sys.stderr,
    )

    if args.summary is not None:
        rate = stats["bash_failed"] / stats["bash_total"] if stats["bash_total"] else 0.0
        print(
            f"bash invocations: {stats['bash_total']}"
            f" ({stats['bash_failed']} failed, {rate:.1%})"
        )
        print("per-tool failure counts (non-bash successes are not parsed; no rates):")
        for tool, failed in sorted(stats["tool_failures"].items(), key=lambda kv: -kv[1]):
            print(f"  {tool:<12} {failed:>7} failed")
        print(f"top {args.summary} bash stems by failure count:")
        top = sorted(stats["stem_failures"].items(), key=lambda kv: -kv[1])[: args.summary]
        for stem, failed in top:
            total = stats["stem_totals"].get(stem, 0)
            rate = failed / total if total else 0.0
            print(f"  {failed:>6} / {total:>7}  ({rate:6.1%})  {stem}")


if __name__ == "__main__":
    main()
