# installers

Per-tool install scripts sourced in order by the root `install.sh`.

## Key files

- **`lib.sh`** — shared helpers sourced by every installer: `ensure_link` (symlink), `ensure_clone` / `ensure_vendor` (shallow git clone, jj-colocate for vendor), `ensure_command` (install a binary if absent, ignoring shims), `ensure_json` (idempotent jq patch). Also exports `DOTFILES_DIR` and prepends `bin`/`~/.local/bin` to `PATH`.
- **Tool installers** — `shell.sh`, `mise.sh`, `docker.sh`, `sops.sh`, `jj.sh`, `tmux.sh`, `nvim.sh`, `claude.sh`, `opencode.sh`, plus others (`ghostty.sh`, `voxtype.sh`, `tt.sh`, `whatsapp.sh`). `docker.sh` installs Docker Engine from Docker's own repo and puts the login user in the `docker` group — never Ubuntu's `docker.io`, whose `containerd` conflicts with `containerd.io` and which ships no buildx or compose plugin. Group membership is what SSH-driven tooling (Pulumi's docker provider, the envoy deploy) needs, since it gets no tty for sudo.
- **Role-specific installer** — `forward.sh` is invoked as a command by machine installers: `serve` installs the devbox file server, while `daemon` installs the laptop URL opener and config. Root `install.sh` does not invoke it because the role must be explicit.

## Conventions

- Start each script with `#!/bin/bash`, `set -euo pipefail`, and `source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"`.
- Use the `lib.sh` helpers instead of hand-rolling symlinks, clones, or JSON edits.
- Keep installers idempotent — running twice must be safe.

## How changes take effect

Root `install.sh` sources its listed installers. Machine-specific setup belongs
in the corresponding `devbox/install.sh` or `laptop/install.sh`; those scripts
can use `lib.sh` for shared installer behavior. Config symlinks apply on next
tool start; binaries install into `PATH` immediately.
