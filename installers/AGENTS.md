# installers

Per-tool install scripts sourced in order by the root `install.sh`.

## Key files

- **`lib.sh`** — shared helpers sourced by every installer: `ensure_link` (symlink), `ensure_clone` / `ensure_vendor` (shallow git clone, jj-colocate for vendor), `ensure_command` (install a binary if absent, ignoring shims), `ensure_json` (idempotent jq patch). Also exports `DOTFILES_DIR` and prepends `bin`/`~/.local/bin` to `PATH`.
- **Tool installers** — `shell.sh`, `mise.sh`, `docker.sh`, `sops.sh`, `jj.sh`, `tmux.sh`, `nvim.sh`, `claude.sh`, `opencode.sh`, plus others (`ghostty.sh`, `voxtype.sh`, `knives.sh`). `docker.sh` installs Docker Engine from Docker's own repo and puts the login user in the `docker` group — never Ubuntu's `docker.io`, whose `containerd` conflicts with `containerd.io` and which ships no buildx or compose plugin. Group membership is what SSH-driven tooling (Pulumi's docker provider, the envoy deploy) needs, since it gets no tty for sudo.
- **Role-specific installers** — invoked as commands by machine installers, never by root `install.sh`, because the role must be explicit. `forward.sh`: `serve` installs the devbox file server, `daemon` installs the laptop URL opener and config. `whatsapp.sh`: installs the WhatsApp MCP daemon user unit (`whatsapp/`) on the laptop, the one machine that holds the paired WhatsApp session, and registers its loopback MCP client in that machine's `~/.omp/agent/.mcp.json` (omp reads it beside the symlinked `mcp.json`; the shared `omp/mcp.json` never lists a server only one machine can reach). `omp-embed.sh` (devbox): runs one `text-embeddings-inference` Docker container (`omp-embed`, model `BAAI/bge-base-en-v1.5`, `127.0.0.1:8087`, `--restart unless-stopped`) and writes `~/.config/omp-embed/endpoint`; `shims/omp` exports `MNEMOPI_EMBEDDING_API_URL` from that file whenever the server answers, so no session spawns its own ~1 GB `__omp_worker_mnemopi_embed`. Re-running it is safe: same image → `docker start`; new image → recreate.

## Conventions

- Start each script with `#!/bin/bash`, `set -euo pipefail`, and `source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"`.
- Use the `lib.sh` helpers instead of hand-rolling symlinks, clones, or JSON edits.
- Keep installers idempotent — running twice must be safe.

## How changes take effect

Root `install.sh` sources its listed installers. Machine-specific setup belongs
in the corresponding `devbox/install.sh` or `laptop/install.sh`; those scripts
can use `lib.sh` for shared installer behavior. Config symlinks apply on next
tool start; binaries install into `PATH` immediately.
