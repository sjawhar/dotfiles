# scripts

Standalone utility scripts and small tools.

## Contents

Executables covering several areas:

- **OpenCode/Claude sessions** — `oc`, `oco`, `cco`, `cld`, `claude-session-repair.py`, `claude-transcript-times`, `sanitize-opencode-db`, `claudeforge-install`.
- **tmux** — `tmux-dev-group`, `tmux-restore`, `tmux-snapshot`, `tmux-osc52-copy`, `tmux-urls`.
- **Monitoring / system** — `ephemeral-monitor`, `mem-usage`, `resource-warnings`, `fix-monitors`, `jj-agent-status`.
- **Misc** — `envoy`, `vendor-update` (rebases `vendor/*` clones), `mdview` / `mdview-server.py`, `joycon`, `brave-hw-encode`, `test-gh-routing`.

## Conventions

- Scripts are executable and self-contained; put a `#!` line and `set -euo pipefail` (bash) or the appropriate interpreter at the top.
- Match the naming and interpreter style of neighboring scripts.

## How changes take effect

This directory is on `PATH` (prepended in `.bashrc`), so scripts run by name in interactive shells. New scripts need the executable bit; no install step or symlink.
