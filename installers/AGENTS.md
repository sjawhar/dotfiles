# installers

Per-tool install scripts sourced in order by the root `install.sh`, plus a few
dual-mode helpers reused by machine-specific installers.

## Key files

- **`lib.sh`** — shared helpers sourced by every installer: `ensure_link` (symlink), `ensure_clone` / `ensure_vendor` (shallow git clone, jj-colocate for vendor), `ensure_command` (install a binary if absent, ignoring shims), `ensure_json` (idempotent jq patch). Also exports `DOTFILES_DIR` and prepends `bin`/`~/.local/bin` to `PATH`.
- **Tool installers** — `shell.sh`, `mise.sh`, `sops.sh`, `jj.sh`, `tmux.sh`, `nvim.sh`, `claude.sh`, `opencode.sh`, plus others (`ghostty.sh`, `voxtype.sh`, `tt.sh`, `whatsapp.sh`).

## Conventions

- Start each script with `#!/bin/bash`, `set -euo pipefail`, and `source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"`.
- Use the `lib.sh` helpers instead of hand-rolling symlinks, clones, or JSON edits.
- Keep installers idempotent — running twice must be safe.

## How changes take effect

Root `install.sh` sources its listed installers. Machine-specific installers can
source a dual-mode helper such as `forward.sh`: sourcing it installs the shared
binary, while executing it directly also sets up the devbox file server. Config
symlinks it creates apply on next tool start; binaries install into `PATH`
immediately.
