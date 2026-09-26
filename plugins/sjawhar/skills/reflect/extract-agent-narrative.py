#!/usr/bin/env python3
"""
Render every Oh My Pi session transcript in a window (top-level sessions AND
nested subagent sidecar files under each session dir) as a readable agent-side
narrative, plus one digest row per file.

Stage 1 of the reflect skill's agent-side problem discovery. The reader picks
narratives from the digest (largest, most tool errors, most retries, most
step-back language, longest gaps) and reads them end to end; this script only
makes that read mechanical. Selection is by file mtime, like
extract-tool-errors.py, so a long-lived session contributes its whole history.

Narrative (`<out>/<session-stem>[/<sidecar-path>].md`), one line per event, in
transcript order:
  [MM-DD HH:MM] <text>            assistant text, in full
  [thinking: <first 200 chars>]   assistant reasoning block
  > tool(args summary) // intent  tool call, summary <= 200 chars
  < ok (N chars)                  tool result without an error
  < skipped tool: <reason>        call the harness never ran (pending advisory /
                                    queued user message); counted separately
  < ERROR tool (exit N):          failed result (isError / non-zero exit) or a
    <text, <= 2,000 chars>          result carrying error markers; read/grep/glob
                                    output is file content and is not marker-scanned
  # USER: [MM-DD HH:MM] <300>     human turn (also `$ cmd` for TUI shell runs)
  # NOTICE: <kind>: <300>         harness-authored turn: envoy, advisor, dispatch,
                                    async-result, developer reminders, XML notices
  >> spawn <name>                 subagent dispatch (task tool)
  << result <name> <status>       subagent settlement (async-result / hub snapshot)
  -- gap Nm --                    > 10 min between consecutive records
  -- compaction --  -- model: X --  -- session exit: reason --

Digest (`<out>/digest.jsonl`), one row per file: file, session, parent, project,
title, cwd, turns, bytes, first_ts, last_ts, tool_calls, tool_errors, skipped_calls,
distinct_error_strings (top 5 [error line, count]), retry_runs (runs of >= 3
consecutive bash calls sharing one command stem), gaps_over_10m {count,
total_minutes}, stepback_markers (assistant text blocks matching the step-back
regex), compactions, subagent_failures, narrative_path, narrative_bytes.

Usage:
    python extract-agent-narrative.py --out DIR [--days N | --since 'YYYY-MM-DD HH:MM']
                                      [--sessions-dir PATH]
"""

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


def get_omp_sessions_dir() -> Path:
    return Path.home() / ".omp" / "agent" / "sessions"


def load_sibling(filename: str):
    """Import a sibling helper script (hyphenated name) as a module."""
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_tool_errors = load_sibling("extract-tool-errors.py")
_user_messages = load_sibling("extract-user-messages.py")
normalize_stem = _tool_errors.normalize_stem
is_failure = _tool_errors.is_failure
redact = _tool_errors.redact
NOISE_PATTERNS = _user_messages.NOISE_PATTERNS

GAP_SECONDS = 10 * 60
ASSISTANT_TEXT_TS = "%m-%d %H:%M"

# Tool results whose text is file content rather than an outcome; never marker-scanned.
CONTENT_TOOLS = {"read", "grep", "glob"}

TRACEBACK = "Traceback (most recent call last)"
ERROR_MARKER_RE = re.compile(
    r"Traceback \(most recent call last\)"
    r"|^\s*(?:error|Error|ERROR|fatal|FATAL|panic)[: ]"
    r"|^\s*\w*(?:Error|Exception)\b:"
    r"|\bFAILED\b"
    r"|command not found"
    r"|No such file or directory"
    r"|Permission denied"
    r"|[Tt]imed out",
    re.M,
)
# Harness trailer lines appended to bash output; never the error itself.
TRAILER_RE = re.compile(r"^(?:Wall time:|Command exited with code|\[Some lines truncated)")

STEPBACK_RE = re.compile(
    r"step back|tried .* failed|workaround|abandon|blocked|waiting on|cannot|unable to|retry",
    re.I | re.S,
)

TASK_RESULT_RE = re.compile(r'<task-result\b[^>]*?\bid="([^"]+)"[^>]*?\bstatus="([^"]+)"')


def parse_ts(value) -> float | None:
    """ISO-8601 (with Z) or epoch-millis -> epoch seconds."""
    if isinstance(value, (int, float)):
        return value / 1000.0 if value > 1e11 else float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def fmt_ts(epoch: float | None, fmt: str = ASSISTANT_TEXT_TS) -> str:
    if epoch is None:
        return "??-?? ??:??"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(fmt)


def iso_ts(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text") or ""
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def one_line(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def args_summary(name: str, args: dict, limit: int = 200) -> str:
    if name == "bash":
        return one_line(str(args.get("command", "")), limit)
    parts = []
    for key, value in args.items():
        if key == "i":
            continue
        if not isinstance(value, str):
            value = json.dumps(value)
        parts.append(f"{key}={value}")
    return one_line(" ".join(parts), limit)


def error_key(text: str, limit: int = 120) -> str:
    """The line that names the error: the exception line after a traceback, else
    the first marker hit, else the first non-empty line."""
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not TRAILER_RE.match(ln)]
    if not lines:
        return "<empty>"
    if TRACEBACK in text:
        return one_line(lines[-1], limit)
    for line in lines:
        if ERROR_MARKER_RE.search(line):
            return one_line(line, limit)
    return one_line(lines[0], limit)


def is_notice(text: str) -> bool:
    if text.lstrip().startswith("<"):
        return True
    return any(pattern.search(text) for pattern in NOISE_PATTERNS.values())


class Narrative:
    """Streams one transcript into a narrative file while accumulating digest stats."""

    def __init__(self, out_path: Path):
        self.out_path = out_path
        self.fh = None
        self.prev_ts = None
        self.first_ts = None
        self.last_ts = None
        self.session_id = None
        self.title = None
        self.cwd = None
        self.turns = 0
        self.tool_calls = 0
        self.tool_errors = 0
        self.skipped_calls = 0
        self.error_strings = Counter()
        self.retry_runs = 0
        self._run_stem = None
        self._run_len = 0
        self.gap_count = 0
        self.gap_seconds = 0.0
        self.stepback = 0
        self.compactions = 0
        self.subagent_failures = 0
        self._calls = {}  # toolCallId -> tool name

    def open(self):
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.out_path, "w", encoding="utf-8")

    def close(self):
        self.fh.close()

    def emit(self, line: str):
        self.fh.write(line)
        self.fh.write("\n")

    def clock(self, record: dict):
        ts = parse_ts(record.get("timestamp"))
        if ts is None:
            return
        if self.first_ts is None:
            self.first_ts = ts
        if self.prev_ts is not None and ts - self.prev_ts > GAP_SECONDS:
            gap = ts - self.prev_ts
            self.gap_count += 1
            self.gap_seconds += gap
            self.emit(f"-- gap {int(gap // 60)}m ({fmt_ts(self.prev_ts, '%H:%M')} → {fmt_ts(ts, '%H:%M')}) --")
        # Monotonic: queued notices (envoy) carry their arrival time but are appended later.
        if self.prev_ts is None or ts > self.prev_ts:
            self.prev_ts = ts
            self.last_ts = ts

    def record(self, obj: dict):
        kind = obj.get("type")
        self.clock(obj)
        if kind == "message":
            self.message(obj.get("message"), parse_ts(obj.get("timestamp")))
        elif kind == "custom_message":
            self.notice(obj.get("customType") or "custom", text_of(obj.get("content")))
        elif kind == "compaction":
            self.compactions += 1
            self.emit("-- compaction --")
        elif kind == "session":
            self.session_id = obj.get("id") or self.session_id
            self.cwd = obj.get("cwd") or self.cwd
            self.title = obj.get("title") or self.title
        elif kind in ("title", "title_change"):
            self.title = obj.get("title") or self.title
        elif kind == "model_change":
            self.emit(f"-- model: {obj.get('model')} --")
        elif kind == "custom" and obj.get("customType") == "session_exit":
            data = obj.get("data") or {}
            self.emit(f"-- session exit: {data.get('reason')} ({data.get('kind')}) --")

    def notice(self, kind: str, text: str):
        self.subagent_results(text)
        self.emit(f"# NOTICE: {kind}: {one_line(redact(text), 300)}")

    def subagent_results(self, text: str):
        for name, status in TASK_RESULT_RE.findall(text):
            if status != "completed":
                self.subagent_failures += 1
            self.emit(f"<< result {name} {status}")

    def message(self, msg, ts: float | None):
        if not isinstance(msg, dict):
            return
        role = msg.get("role")
        if role == "user":
            self.turns += 1
            text = text_of(msg.get("content"))
            if is_notice(text):
                self.notice("user-role", text)
            else:
                self.emit(f"# USER: [{fmt_ts(ts)}] {one_line(redact(text), 300)}")
        elif role == "assistant":
            self.turns += 1
            self.assistant(msg.get("content"), ts)
        elif role == "toolResult":
            self.tool_result(msg)
        elif role == "developer":
            self.notice("developer", text_of(msg.get("content")))
        elif role == "bashExecution":
            self.emit(f"# USER: [{fmt_ts(ts)}] $ {one_line(redact(str(msg.get('command', ''))), 300)}")

    def assistant(self, content, ts: float | None):
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            return
        for item in content:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "text":
                text = item.get("text") or ""
                if not text.strip():
                    continue
                if STEPBACK_RE.search(text):
                    self.stepback += 1
                self.emit("")
                self.emit(f"[{fmt_ts(ts)}] {redact(text).rstrip()}")
                self.emit("")
            elif kind == "thinking":
                self.emit(f"[thinking: {one_line(redact(item.get('thinking') or ''), 200)}]")
            elif kind == "toolCall":
                self.tool_call(item)

    def tool_call(self, item: dict):
        name = item.get("name") or "<unknown>"
        args = item.get("arguments") or {}
        if not isinstance(args, dict):
            args = {"arguments": args}
        self.tool_calls += 1
        self._calls[item.get("id")] = name
        intent = item.get("intent") or args.get("i") or ""
        suffix = f" // {one_line(redact(str(intent)), 80)}" if intent else ""
        self.emit(f"> {name}({args_summary(name, args)}){suffix}")
        if name == "bash":
            self.track_retry(normalize_stem(str(args.get("command", ""))))
        if name == "task":
            tasks = args.get("tasks")
            names = (
                [t.get("name") for t in tasks if isinstance(t, dict)]
                if isinstance(tasks, list)
                else [args.get("name")]
            )
            for spawned in names:
                self.emit(f">> spawn {spawned or '<unnamed>'}")

    def track_retry(self, stem: str):
        if stem == self._run_stem:
            self._run_len += 1
            if self._run_len == 3:
                self.retry_runs += 1
        else:
            self._run_stem = stem
            self._run_len = 1

    def tool_result(self, msg: dict):
        tool = msg.get("toolName") or self._calls.get(msg.get("toolCallId")) or "<unknown>"
        self._calls.pop(msg.get("toolCallId"), None)
        details = msg.get("details") or {}
        text = text_of(msg.get("content"))
        if details.get("source") == "interrupt_skipped":
            # Harness never ran the call (advisory / queued user message); not a tool error.
            self.skipped_calls += 1
            self.emit(f"< skipped {tool}: {one_line(text, 80)}")
            return
        self.subagent_results(text)
        failed = is_failure(msg, details)
        if not failed and tool not in CONTENT_TOOLS and ERROR_MARKER_RE.search(text):
            failed = True
        if not failed:
            self.emit(f"< ok ({len(text)} chars)")
            return
        self.tool_errors += 1
        clean = redact(text)
        self.error_strings[error_key(clean)] += 1
        exit_code = details.get("exitCode")
        label = f" (exit {exit_code})" if exit_code is not None else ""
        if details.get("timedOut"):
            label += " (timed out)"
        self.emit(f"< ERROR {tool}{label}:")
        self.emit(clean[:2000].rstrip())

    def digest(self, rel: str, project: str, parent: str | None, size: int) -> dict:
        return {
            "file": rel,
            "session": self.session_id,
            "parent": parent,
            "project": project,
            "title": self.title,
            "cwd": self.cwd,
            "turns": self.turns,
            "bytes": size,
            "first_ts": iso_ts(self.first_ts),
            "last_ts": iso_ts(self.last_ts),
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "skipped_calls": self.skipped_calls,
            "distinct_error_strings": self.error_strings.most_common(5),
            "retry_runs": self.retry_runs,
            "gaps_over_10m": {
                "count": self.gap_count,
                "total_minutes": round(self.gap_seconds / 60.0, 1),
            },
            "stepback_markers": self.stepback,
            "compactions": self.compactions,
            "subagent_failures": self.subagent_failures,
            "narrative_path": str(self.out_path),
            "narrative_bytes": self.out_path.stat().st_size,
        }


def session_uuid(stem: str) -> str:
    """`2026-09-04T21-49-50-837Z_<uuid>` -> `<uuid>`; plain stems pass through."""
    return stem.rsplit("_", 1)[-1]


def process_file(fp: str, root: str, out_dir: Path, stats: dict) -> dict | None:
    rel = os.path.relpath(fp, root)
    parts = rel.split(os.sep)
    project = parts[0]
    within = parts[1:]  # [<stem>.jsonl] or [<stem>, ..., <sidecar>.jsonl]
    top_stem = within[0][: -len(".jsonl")] if len(within) == 1 else within[0]
    parent = None if len(within) == 1 else session_uuid(top_stem)
    out_path = out_dir.joinpath(*within).with_suffix(".md")

    narrative = Narrative(out_path)
    narrative.open()
    narrative.emit(f"# narrative: {rel}")
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if len(line) < 3:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    stats["json_errors"] += 1
                    continue
                if isinstance(obj, dict):
                    narrative.record(obj)
    except OSError as e:
        print(f"  Error reading {fp}: {e}", file=sys.stderr)
        stats["file_errors"] += 1
        narrative.close()
        return None
    narrative.close()
    if narrative.session_id is None:
        narrative.session_id = session_uuid(within[-1][: -len(".jsonl")]) if parent is None else None
    return narrative.digest(rel, project, parent, os.stat(fp).st_size)


def main():
    parser = argparse.ArgumentParser(
        description="Render omp session transcripts as agent-side narratives with a digest"
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
        required=True,
        help="Output directory: one narrative .md per transcript plus digest.jsonl",
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
    total_bytes = 0
    for dirpath, _dirnames, filenames in os.walk(sessions_dir):
        for name in filenames:
            if not name.endswith(".jsonl"):
                continue
            fp = os.path.join(dirpath, name)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            if st.st_mtime >= cutoff:
                files.append(fp)
                total_bytes += st.st_size
    files.sort()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {
        "top_level": 0,
        "sidecar": 0,
        "json_errors": 0,
        "file_errors": 0,
        "narrative_bytes": 0,
        "digest_rows": 0,
    }
    root = str(sessions_dir)
    started = time.monotonic()
    done_bytes = 0
    with open(out_dir / "digest.jsonl", "w", encoding="utf-8") as digest:
        for i, fp in enumerate(files, 1):
            row = process_file(fp, root, out_dir, stats)
            done_bytes += os.stat(fp).st_size
            if row is not None:
                stats["top_level" if row["parent"] is None else "sidecar"] += 1
                stats["narrative_bytes"] += row["narrative_bytes"]
                stats["digest_rows"] += 1
                digest.write(json.dumps(row) + "\n")
            if i % 500 == 0:
                elapsed = time.monotonic() - started
                print(
                    f"  {i}/{len(files)} files, {done_bytes / 1e9:.2f}/{total_bytes / 1e9:.2f} GB,"
                    f" {elapsed / 60:.1f} min",
                    file=sys.stderr,
                )

    elapsed = time.monotonic() - started
    print(
        f"window: file mtime >= {datetime.fromtimestamp(cutoff, tz=timezone.utc):%Y-%m-%d %H:%M} UTC"
        f" | {len(files)} files ({stats['top_level']} top-level, {stats['sidecar']} sidecar)"
        f" | {total_bytes / 1e9:.2f} GB read | {stats['narrative_bytes'] / 1e6:.1f} MB of narrative"
        f" | digest rows: {stats['digest_rows']} | json errors: {stats['json_errors']}"
        f" | file errors: {stats['file_errors']} | {elapsed / 60:.1f} min",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
