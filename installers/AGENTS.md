# installers

Per-tool install scripts sourced in order by the root `install.sh`.

## Key files

- **`lib.sh`** — shared helpers sourced by every installer: `ensure_link` (symlink), `ensure_clone` / `ensure_vendor` (shallow git clone, jj-colocate for vendor), `ensure_command` (install a binary if absent, ignoring shims), `ensure_json` (idempotent jq patch). Also exports `DOTFILES_DIR` and prepends `bin`/`~/.local/bin` to `PATH`.
- **Tool installers** — `shell.sh`, `mise.sh`, `sops.sh`, `jj.sh`, `tmux.sh`, `nvim.sh`, `claude.sh`, `opencode.sh`, plus others (`ghostty.sh`, `voxtype.sh`, `tt.sh`, `whatsapp.sh`).

## Conventions

- Start each script with `#!/bin/bash`, `set -euo pipefail`, and `source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"`.
- Use the `lib.sh` helpers instead of hand-rolling symlinks, clones, or JSON edits.
- Keep installers idempotent — running twice must be safe.

## How changes take effect

A new installer only runs once it is `source`d from the root `install.sh`. Config symlinks it creates apply on next tool start; binaries install into `PATH` immediately.
