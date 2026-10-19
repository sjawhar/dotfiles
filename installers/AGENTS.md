# installers

Per-tool install scripts sourced in order by the root `install.sh`.

## Key files

- **`lib.sh`** — shared helpers sourced by every installer: `ensure_link` (symlink; replaces a plain file only when it is identical to the source, and fails on a differing file or a real directory), `ensure_clone` / `ensure_vendor` (shallow git clone; `ensure_vendor` also follows the URL and converges a pinned ref), `ensure_command` (install a binary if absent, ignoring shims), `ensure_json` (idempotent jq patch), `arm_user_timer <unit-dir> <name>` (a watch installer's timer: refuses inside an agentbox and when either `~/.dotfiles/<unit-dir>/<name>.{service,timer}` is missing, links both from `~/.dotfiles` rather than `DOTFILES_DIR`, runs `daemon-reload` and `enable --now`, and proves the timer in `list-timers`; the installer keeps its own preconditions). Also exports `DOTFILES_DIR` and prepends `bin`/`~/.local/bin` to `PATH`.
- **Tool installers** — `shell.sh`, `mise.sh`, `docker.sh`, `sops.sh`, `jj.sh`, `tmux.sh`, `nvim.sh`, `claude.sh`, `omp.sh`, plus others (`ghostty.sh`, `voxtype.sh`, `knives.sh`). `omp.sh` also clones the `vendor/` checkouts behind `skills-sources.json`. `docker.sh` installs Docker Engine from Docker's own repo and puts the login user in the `docker` group — never Ubuntu's `docker.io`, whose `containerd` conflicts with `containerd.io` and which ships no buildx or compose plugin. Group membership is what SSH-driven tooling (Pulumi's docker provider, the envoy deploy) needs, since it gets no tty for sudo.
- **Role-specific installers** — invoked as commands by machine installers, never by root `install.sh`, because the role must be explicit. `forward.sh`: `serve` installs the devbox file server with `forward/config-serve-<host>.toml` (oryx, sami-leanperf, sami-rocqperf) and fails with a message when the host has no pair of its own, since there is no default pair; `daemon` installs the laptop URL opener, plus one `forward-daemon@<pair>` per `forward/config-daemon-<pair>.toml`, because forward trusts a single peer per daemon, and removes the old default daemon's unit link and `~/.config/forward/config.toml` link where an earlier install left them. A pair whose daemon config listens on loopback is a tunnelled pair, carried over SSH: `daemon` also enables `forward-tunnel@<pair>`, which holds `ssh -N <pair>-tunnel` open, and that Host block in `~/.ssh/config.local` (machine-local, not in this repo) maps every forward port to the same port on the far side's loopback. Tunnelled connections arrive from 127.0.0.1, so every such pair's peer is 127.0.0.1 and each takes its own listen address on the laptop (sami-leanperf 127.0.0.1, sami-rocqperf 127.0.0.2, the same at the devbox end, where it names the preview URLs), plus its own bridge and grant ports, which the laptop dials at 127.0.0.1. `omp-embed.sh` (devbox): runs one `text-embeddings-inference` Docker container (`omp-embed`, model `BAAI/bge-base-en-v1.5`, `127.0.0.1:8087`, `--restart unless-stopped`) and writes `~/.config/omp-embed/endpoint`; `shims/omp` exports `MNEMOPI_EMBEDDING_API_URL` from that file whenever the server answers, so no session spawns its own ~1 GB `__omp_worker_mnemopi_embed`. Re-running it is safe: same image → `docker start`; new image → recreate.
- **`agent-secrets.sh`** (devbox, a role installer like those above; `devbox/install.sh` runs it) — links, enables and starts the secrets broker's host helper (`agent-secrets/`), only on a machine that runs agent sessions: a machine without it (the laptop) keeps secretsd alone, with no `AGENT_SECRETS_*` in its shells and no session registration. It restarts the helper only when the pinned version or the helper's files changed, never while host sessions are registered (unless `AGENT_SECRETS_HELPER_RESTART_WITH_SESSIONS=1`), and records the restart only once it succeeded; a refused restart is a warning, not a failed install.

## Conventions

- Start each script with `#!/bin/bash`, `set -euo pipefail`, and `source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"`.
- Use the `lib.sh` helpers instead of hand-rolling symlinks, clones, or JSON edits.
- Keep installers idempotent — running twice must be safe.

## How changes take effect

Root `install.sh` sources its listed installers. Machine-specific setup belongs
in the corresponding `devbox/install.sh` or `laptop/install.sh`; those scripts
can use `lib.sh` for shared installer behavior. Config symlinks apply on next
tool start; binaries install into `PATH` immediately.
