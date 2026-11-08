"""Read voxtype's transcription history for the tray and the launcher plugin.

history.jsonl is appended by voxtype-post-process, oldest first, one
{"ts": "<UTC ISO-8601>", "text": <refined>, "raw": <engine output>} per line.
"""

import json
import os
from datetime import datetime

HISTORY_FILE = os.path.join(
    os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
    "voxtype",
    "history.jsonl",
)

_TAIL_BLOCK = 65536


def _parse(line):
    """(ts, text) for one history line, or None for a blank or malformed one."""
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    text = entry.get("text") if isinstance(entry, dict) else None
    if not text:
        return None
    return entry.get("ts", ""), text


def stamp():
    """Changes whenever the history file is appended to; None if it is missing."""
    try:
        st = os.stat(HISTORY_FILE)
    except FileNotFoundError:
        return None
    return st.st_mtime_ns, st.st_size


def recent(n):
    """The n newest entries, newest first, reading only the end of the file."""
    try:
        f = open(HISTORY_FILE, "rb")
    except FileNotFoundError:
        return []
    with f:
        pos = f.seek(0, os.SEEK_END)
        buf = b""
        # n + 1 newlines guarantee n whole lines after the partial first one.
        while pos > 0 and buf.count(b"\n") <= n:
            step = min(_TAIL_BLOCK, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
    lines = buf.splitlines()
    if pos > 0:
        lines = lines[1:]
    entries = []
    for line in reversed(lines):
        entry = _parse(line)
        if entry:
            entries.append(entry)
            if len(entries) == n:
                break
    return entries


def load_all():
    """Every entry, newest first."""
    try:
        with open(HISTORY_FILE, "rb") as f:
            entries = [e for e in map(_parse, f) if e]
    except FileNotFoundError:
        return []
    entries.reverse()
    return entries


def when(ts):
    """Local time of an entry: 08:28 today, Nov 9 18:33 this year, else with the year."""
    try:
        t = datetime.fromisoformat(ts).astimezone()
    except ValueError:
        return ts
    now = datetime.now().astimezone()
    if t.date() == now.date():
        return t.strftime("%H:%M")
    if t.year == now.year:
        return f"{t:%b} {t.day} {t:%H:%M}"
    return t.strftime("%Y-%m-%d %H:%M")


def preview(text, width):
    """Text on one line, cut at a word boundary to at most width characters."""
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    cut = flat[: width - 1]
    space = cut.rfind(" ")
    if space > width // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:") + "…"
