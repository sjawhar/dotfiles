# scripts

Standalone utility scripts and small tools.

## Contents

Executables covering several areas:

- **OpenCode/Claude/omp sessions** — `agentbox` (`new <repo> <cmd>|sh|ls|doctor|rebuild|restart`: one Sysbox box per agent session in a fresh jj workspace of `~/src/<repo>`; `omp` inside it is the same `shims/omp`, run under `agentbox-run.ts` so `agentbox restart` can relaunch it in place on the current pin and plugins; the box dies with the session, and its workspace and knives claims go with it), `agentbox-run.ts` (the box's main process: omp's parent, relaunches it on SIGUSR1 — `agentbox restart` inside a box — and otherwise exits with it), `oc`, `oco`, `ompo` (omp under a named profile, billed only to that profile's `*_API_KEY_<NAME>` secrets; see `shims/omp`), `cld`, `claude-statusline` (Claude Code's status line, set in `.claude/settings.json`: the context the next request carries, e.g. `ctx 184k/1M (18%)`, from the `context_window` Claude Code pipes in), `claude-session-repair.py`, `claude-transcript-times`, `sanitize-opencode-db`, `claudeforge-install`.
- **tmux** — `tmux-dev-group`, `tmux-restore`, `tmux-snapshot`, `tmux-osc52-copy`, `tmux-urls`, `tmux-resurrect-omp` (tmux-resurrect save hook: records each omp pane as `omp --resume <id>`, or `agentbox omp <repo> --resume <id>` for a pane already running in a box, so a server restart brings the sessions back, not just the layout).
- **Monitoring / system** — `ephemeral-monitor`, `mem-usage`, `resource-warnings`, `fix-monitors`, `jj-agent-status`, `fix-watchman` (unwatches Watchman roots whose sync is broken, which otherwise adds ~60s to every jj command in that workspace).
- **Version control** — `git-identity` (sets the git *and* jj commit identity for a repo together, so signing stays intact).
- **Misc** — `envoy`, `vendor-update` (rebases `vendor/*` clones), `mdview` / `mdview-server.py`, `joycon`, `brave-hw-encode`, `test-gh-routing`.

## Conventions

- Scripts are executable and self-contained; put a `#!` line and `set -euo pipefail` (bash) or the appropriate interpreter at the top.
- Match the naming and interpreter style of neighboring scripts.

## How changes take effect

This directory is on `PATH` (prepended in `.bashrc`), so scripts run by name in interactive shells. New scripts need the executable bit; no install step or symlink.
