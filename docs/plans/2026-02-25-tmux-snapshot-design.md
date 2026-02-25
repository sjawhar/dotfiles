# tmux-snapshot — Tmux Workspace Session Saver

## Problem

After a reboot or environment teardown, there's no record of which AI coding sessions (OpenCode, Claude Code) were running in which tmux panes. tmux-resurrect restores pane layouts and working directories, but not the AI session IDs needed to resume conversations.

## Solution

A Python script that gracefully shuts down AI sessions in each tmux pane, captures the session IDs from their exit output, and writes a JSON snapshot file.

## How It Works

Two-pass approach (pane indices shift as panes close, so we capture all state before quitting anything):

**Pass 1 — Snapshot all panes:**
1. User opens a new tmux window and runs `tmux-snapshot`
2. Script enumerates all panes in the current tmux session, excluding its own pane
3. For each pane, records baseline state: working directory, running command, pane title, and the stable `pane_id` (e.g., `%45` — survives index renumbering)
4. Detects which panes are AI sessions (by title pattern) and marks them for shutdown

**Pass 2 — Shutdown AI sessions and capture IDs:**
5. For each AI pane (using stable `pane_id` for targeting):
   - Sends quit keystrokes via `tmux send-keys -t <pane_id>`
   - Polls `tmux capture-pane -t <pane_id>` until the exit message with session ID appears
   - Parses the session ID from the captured output
6. Writes snapshot JSON to `~/.tmux-workspace/snapshot.json`

## AI Session Detection

A pane is considered an AI session if:
- **OpenCode**: Pane title starts with `OC | ` (set by OpenCode's TUI)
- **Claude Code**: Process tree from pane PID contains a `claude` process, or pane title matches Claude patterns

Detection is best-effort. If detection fails, the pane is recorded as a plain shell pane.

## Quit Mechanisms

### OpenCode
- **Send**: `Ctrl+C` via `tmux send-keys -t <pane> C-c`
- **Exit output pattern**: Line containing `opencode -s <session_id>`
- **Regex**: `opencode -s (ses_\S+)`

### Claude Code
- **Send**: `Ctrl+C` twice via `tmux send-keys -t <pane> C-c C-c`
- **Exit output pattern**: Line containing `claude -r <session_id>` (to verify — no Claude sessions running currently)
- **Regex**: `claude -r (\S+)` (session IDs are UUIDs or similar)

## Polling Strategy

After sending quit keystrokes (in pass 2, using stable `pane_id` targets):
- Poll `tmux capture-pane -t <pane_id> -p` every 1 second
- Look for the session ID pattern in the captured output
- Timeout after 30 seconds per pane (configurable)
- If timeout: record pane with `ai_session: null` and a warning, continue to next pane
- Process AI panes sequentially (simpler, avoids tmux command races)

## Output Format

File: `~/.tmux-workspace/snapshot.json`

```json
{
  "version": 1,
  "captured_at": "2026-02-25T02:30:00Z",
  "hostname": "ip-172-31-40-201",
  "tmux_session": "dev",
  "panes": [
    {
      "window_index": 1,
      "window_name": "agent-c",
      "pane_index": 1,
      "cwd": "/home/ubuntu/agent-c/default",
      "command": "bash",
      "ai_session": {
        "type": "opencode",
        "session_id": "ses_36dac164cffeJwv3qsIU6V6L88",
        "title": "I'll check the current branch status ...",
        "resume_cmd": "opencode -s ses_36dac164cffeJwv3qsIU6V6L88"
      }
    },
    {
      "window_index": 1,
      "window_name": "agent-c",
      "pane_index": 2,
      "cwd": "/home/ubuntu/agent-c/wt",
      "command": "bash",
      "ai_session": {
        "type": "opencode",
        "session_id": "ses_36dac164cffeABC123",
        "title": "wt CLI task development tool",
        "resume_cmd": "opencode -s ses_36dac164cffeABC123"
      }
    },
    {
      "window_index": 3,
      "window_name": "misc",
      "pane_index": 2,
      "cwd": "/home/ubuntu/agent-c/recover",
      "command": "bash",
      "ai_session": null
    }
  ]
}
```

## Implementation

- **Language**: Python 3.11+ (stdlib only — `subprocess`, `json`, `re`, `pathlib`, `time`, `os`)
- **Location**: `scripts/tmux-snapshot` (executable with `#!/usr/bin/env python3`)
- **Self-exclusion**: Uses `$TMUX_PANE` env var to skip the script's own pane
- **Pane targeting**: Uses tmux's stable `pane_id` (`%N` format) for all `send-keys` and `capture-pane` commands — immune to index renumbering as panes close
- **Scope**: Current tmux session only (not all sessions)
- **Error handling**: Per-pane errors are warnings, never abort the whole snapshot. Panes that fail detection get `ai_session: null`.

## Edge Cases

- **Pane already at shell prompt** (no AI session running): Detected by absence of OC/Claude title patterns. Recorded as plain pane.
- **AI session mid-operation** (running a tool, generating): Ctrl+C should interrupt and begin shutdown. May need a second Ctrl+C if first is caught by a subprocess.
- **Pane running something else** (vim, htop, uv): Recorded as plain pane with command name. No quit attempt.
- **Grouped tmux sessions** (dev/dev2 sharing windows): Script only operates on its own session. If the user runs from `dev`, it captures `dev`'s panes. The grouped session `dev2` shares the same windows, so the data is the same.

## Out of Scope

- **Restore**: Save-only for now. Restore can be a future script that reads the snapshot and runs resume commands.
- **Auto-run**: No tmux hooks or cron. Manual invocation only.
- **devenv-backup integration**: Independent tool. Could be integrated later.
- **YAML output**: JSON only, consistent with devenv-backup patterns.
