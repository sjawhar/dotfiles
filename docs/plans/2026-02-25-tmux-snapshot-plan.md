# tmux-snapshot Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A Python script that snapshots all tmux panes, gracefully quits AI sessions, captures their session IDs, and writes a JSON file.

**Architecture:** Single-file Python script using only stdlib. Two-pass: enumerate panes first, then shut down AI sessions and capture exit output via `tmux send-keys` / `tmux capture-pane`. All pane targeting uses stable `pane_id` (`%N` format).

**Tech Stack:** Python 3.11+ stdlib (`subprocess`, `json`, `re`, `pathlib`, `time`, `os`, `datetime`)

**Design doc:** `docs/plans/2026-02-25-tmux-snapshot-design.md`

---

### Task 1: Script skeleton and pane enumeration (Pass 1)

**Files:**
- Create: `scripts/tmux-snapshot`

**Step 1: Create the executable script with pane enumeration**

```python
#!/usr/bin/env python3
"""Snapshot tmux workspace: capture pane state and AI session IDs."""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_DIR = Path.home() / ".tmux-workspace"
SNAPSHOT_FILE = SNAPSHOT_DIR / "snapshot.json"
POLL_INTERVAL = 1.0  # seconds between capture-pane polls
POLL_TIMEOUT = 30.0  # max seconds to wait for session ID after quit

# Patterns to extract session IDs from exit output
OPENCODE_PATTERN = re.compile(r"opencode -s (ses_\S+)")
CLAUDE_PATTERN = re.compile(r"claude -r (\S+)")


def tmux(*args: str) -> str:
    """Run a tmux command and return stdout."""
    result = subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tmux {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def get_current_session() -> str:
    """Get the tmux session name for the current client."""
    return tmux("display-message", "-p", "#{session_name}").strip()


def enumerate_panes(session: str, exclude_pane_id: str) -> list[dict]:
    """Enumerate all panes in the session, excluding our own.

    Returns list of dicts with pane metadata.
    Uses tmux format strings with a delimiter for reliable parsing.
    """
    fmt = "#{pane_id}\t#{window_index}\t#{window_name}\t#{pane_index}\t#{pane_current_path}\t#{pane_current_command}\t#{pane_title}"
    output = tmux("list-panes", "-s", "-t", session, "-F", fmt)

    panes = []
    for line in output.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t", 6)
        if len(parts) < 7:
            continue
        pane_id, win_idx, win_name, pane_idx, cwd, command, title = parts
        if pane_id == exclude_pane_id:
            continue
        panes.append({
            "pane_id": pane_id,
            "window_index": int(win_idx),
            "window_name": win_name,
            "pane_index": int(pane_idx),
            "cwd": cwd,
            "command": command,
            "title": title,
        })
    return panes
```

**Step 2: Verify it runs**

Run from a tmux pane:
```bash
chmod +x scripts/tmux-snapshot
python3 scripts/tmux-snapshot  # should print nothing yet, but not error
```

**Step 3: Commit**

`jj describe -m "tmux-snapshot: script skeleton with pane enumeration"`

---

### Task 2: AI session detection

**Files:**
- Modify: `scripts/tmux-snapshot`

**Step 1: Add detection functions**

After the `enumerate_panes` function, add:

```python
def detect_ai_type(pane: dict) -> str | None:
    """Detect if a pane is running an AI session.

    Returns 'opencode', 'claude', or None.
    """
    title = pane["title"]
    # OpenCode sets title to "OC | <description>" or "OpenCode"
    if title.startswith("OC | ") or title == "OpenCode":
        return "opencode"
    # Claude Code detection: check pane title patterns
    # Claude doesn't set a distinctive title, so fall back to command check
    # The pane_current_command is 'bash' (the shell), but we can check
    # if claude is a child process via the title or other heuristics
    if "claude" in title.lower():
        return "claude"
    return None
```

**Step 2: Wire detection into enumeration**

After building the panes list in `enumerate_panes`, the caller will use `detect_ai_type` to classify each pane. No change to `enumerate_panes` itself — keep it pure data collection.

**Step 3: Commit**

`jj describe -m "tmux-snapshot: AI session detection by pane title"`

---

### Task 3: Graceful shutdown and session ID capture (Pass 2)

**Files:**
- Modify: `scripts/tmux-snapshot`

**Step 1: Add quit and capture functions**

```python
def send_quit(pane_id: str, ai_type: str) -> None:
    """Send quit keystrokes to an AI session pane."""
    if ai_type == "opencode":
        tmux("send-keys", "-t", pane_id, "C-c")
    elif ai_type == "claude":
        # Claude needs Ctrl+C twice
        tmux("send-keys", "-t", pane_id, "C-c")
        time.sleep(0.5)
        tmux("send-keys", "-t", pane_id, "C-c")


def capture_pane(pane_id: str) -> str:
    """Capture the visible content of a pane."""
    return tmux("capture-pane", "-t", pane_id, "-p")


def wait_for_session_id(pane_id: str, ai_type: str) -> dict | None:
    """Poll pane output for session ID after quit.

    Returns ai_session dict or None on timeout.
    """
    pattern = OPENCODE_PATTERN if ai_type == "opencode" else CLAUDE_PATTERN
    deadline = time.monotonic() + POLL_TIMEOUT

    while time.monotonic() < deadline:
        output = capture_pane(pane_id)
        match = pattern.search(output)
        if match:
            session_id = match.group(1)
            # Extract title from the exit output if available
            title = _extract_title(output, ai_type)
            resume_cmd = f"opencode -s {session_id}" if ai_type == "opencode" else f"claude -r {session_id}"
            return {
                "type": ai_type,
                "session_id": session_id,
                "title": title,
                "resume_cmd": resume_cmd,
            }
        time.sleep(POLL_INTERVAL)

    return None


def _extract_title(output: str, ai_type: str) -> str | None:
    """Try to extract the session title from exit output."""
    if ai_type == "opencode":
        # OpenCode exit format has "Session   <title>" line
        # The title appears with ANSI codes between "Session" and "Continue"
        for line in output.split("\n"):
            # Strip ANSI escape codes for matching
            clean = re.sub(r"\x1b\[[0-9;]*m", "", line)
            if "Session" in clean and "Continue" not in clean:
                # Title is after "Session" with padding
                parts = clean.split("Session", 1)
                if len(parts) > 1:
                    return parts[1].strip() or None
    return None
```

**Step 2: Commit**

`jj describe -m "tmux-snapshot: graceful shutdown and session ID capture"`

---

### Task 4: Main function and JSON output

**Files:**
- Modify: `scripts/tmux-snapshot`

**Step 1: Add main function**

```python
def snapshot() -> dict:
    """Run the two-pass snapshot."""
    my_pane = os.environ.get("TMUX_PANE")
    if not my_pane:
        print("Error: not running inside tmux", file=sys.stderr)
        sys.exit(1)

    session = get_current_session()
    print(f"Snapshotting tmux session: {session}", file=sys.stderr)

    # Pass 1: enumerate all panes
    panes = enumerate_panes(session, exclude_pane_id=my_pane)
    print(f"Found {len(panes)} panes (excluding self)", file=sys.stderr)

    # Classify panes
    ai_panes = []
    for pane in panes:
        pane["_ai_type"] = detect_ai_type(pane)
        if pane["_ai_type"]:
            ai_panes.append(pane)

    print(f"Detected {len(ai_panes)} AI session(s)", file=sys.stderr)

    # Pass 2: shut down AI sessions and capture IDs
    for pane in ai_panes:
        ai_type = pane["_ai_type"]
        pane_id = pane["pane_id"]
        print(f"  Quitting {ai_type} in {pane_id} ({pane['cwd']})...", file=sys.stderr)
        send_quit(pane_id, ai_type)
        ai_session = wait_for_session_id(pane_id, ai_type)
        if ai_session:
            pane["ai_session"] = ai_session
            print(f"    Captured: {ai_session['session_id']}", file=sys.stderr)
        else:
            pane["ai_session"] = None
            print(f"    Warning: timed out waiting for session ID", file=sys.stderr)

    # Build output
    result = {
        "version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "hostname": os.uname().nodename,
        "tmux_session": session,
        "panes": [],
    }
    for pane in panes:
        entry = {
            "window_index": pane["window_index"],
            "window_name": pane["window_name"],
            "pane_index": pane["pane_index"],
            "cwd": pane["cwd"],
            "command": pane["command"],
            "ai_session": pane.get("ai_session"),
        }
        result["panes"].append(entry)

    return result


def main() -> None:
    result = snapshot()

    # Write snapshot file
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nSnapshot saved to {SNAPSHOT_FILE}", file=sys.stderr)

    # Also print to stdout for piping
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
```

**Step 2: Make executable and verify**

```bash
chmod +x scripts/tmux-snapshot
```

Run from a new tmux window to test:
```bash
scripts/tmux-snapshot
```

Expected: Lists panes, detects OC sessions, sends Ctrl+C, captures session IDs, writes JSON.

**Step 3: Commit**

`jj describe -m "tmux-snapshot: main function and JSON output"`

---

### Task 5: Manual testing and polish

**Files:**
- Modify: `scripts/tmux-snapshot` (if needed)

**Step 1: Test with live OpenCode sessions**

1. Open a new tmux window: `Ctrl+B c`
2. Run: `scripts/tmux-snapshot`
3. Verify:
   - All panes listed (excluding the script's own pane)
   - OpenCode panes detected by `OC | ` title prefix
   - Ctrl+C sent, session IDs captured from exit output
   - JSON written to `~/.tmux-workspace/snapshot.json`
   - JSON printed to stdout
4. Check the JSON file contains valid session IDs matching `ses_*` pattern
5. Verify resume commands work: copy a `resume_cmd` from the JSON and run it

**Step 2: Test edge cases**

- Plain shell pane (no AI session): should have `ai_session: null`
- Pane running other process (e.g., `vim`): should be recorded as-is, no quit attempt
- OpenCode mid-operation: Ctrl+C should interrupt and trigger exit

**Step 3: Final commit**

`jj describe -m "feat: add tmux-snapshot workspace session saver"`
