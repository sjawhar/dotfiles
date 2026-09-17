# devpod

Two independent halves: the opt-in **agent box image** (`Dockerfile`, `entrypoint.sh`) that `scripts/agentbox` runs a session in, and a **cloud-init script** for a bare VM (`config.toml` `[devbox]` `user_data`), currently dormant.

## Agent box image

One disposable Sysbox container per **agent session**, working in a jj workspace made for it. `agentbox omp <repo> [omp args…]` names a canonical checkout under `~/src` (`AGENTBOX_SRC`), adds a jj workspace of it at `~/boxes/<box>/<repo>`, and runs `shims/omp` there as the container's main process (`docker run --rm`), so the box lives exactly as long as the session. When omp exits — clean exit, pane kill, launcher killed — the launcher releases every knives claim the session holds (`knives finish`, as that session), snapshots the working copy (`jj st`), forgets the workspace, and removes `~/boxes/<box>`. Uncommitted edits survive as a commit in the canonical repo's op store; the directory does not.

Every omp flag passes straight through. `--resume <id>` has one launcher-side effect: omp ties a session to the directory it started in and refuses to resume one whose directory is gone, so a resumed boxed session gets its workspace back at the same path (the box takes the old name, read from the session file's own `cwd`). A host session started in a mounted directory (`~/.dotfiles`, anything under `~/src`) resumes as is; one started in an unmounted host checkout is omp's to refuse, exactly as on the host.

**The box sees an allow-list of the home directory and nothing else** (`MOUNT_RW`/`MOUNT_RO` in `scripts/agentbox`): the canonical checkouts (`~/src`, plus the target of any symlink in it — `~/src/dotfiles → ~/.dotfiles`), the box's own directory, omp state (`~/.omp`, `~/.config/omp`, `~/.cache/omp`), the dotfiles, the mise tool store and language caches, `~/.aws`, `~/.kube`, `~/.pulumi`, the tool configs a session relies on (forward, jj, secretsd, sops, trajectory-labs, taiga, gws, knives, opencode, claude, mcp-auth, pcscd), `~/.local/bin`, the jj-signing pair read-only, and the host sockets (secretsd, forward, hawk-token) inside an ubuntu-owned per-box `/run/user/$UID`. The user's GitHub login, ssh keys, keyrings, gnupg and docker login are not mounted — not hidden, absent — so an agent inside pushes only as the GitHub App its repo routes to (`gh-app-routes.gitconfig`), and can leave clutter only in the repos themselves or in its own box directory, which is deleted.

AWS is the exception by design (the instance role is meant for agents): `~/.aws` is visible and the box reaches the EC2 metadata service through a socat forwarder the launcher runs on the host as a transient user unit (`agentbox-imds`, bound to the box network's gateway, sources limited to that network); the entrypoint drops container traffic to it inside the box so a sandbox the box starts cannot hold the role. The Envoy HTTP API (`envoy-listener` on the host loopback) reaches the box the same way (`agentbox-envoy`, `ENVOY_URL` points at the gateway); NATS is reachable by name on the box network.

Hawk tokens (the anthropic provider in `omp/models.yml` resolves `!hawk-token`) are host-minted too: hawk keeps the user's Cognito session in the GNOME keyring, which stays out of the box (it also holds the gh login). `agentbox-hawk-token` runs `scripts/hawk-token` once per connection on `/run/user/$UID/agentbox/hawk-token/hawk-token.sock`; the directory is mounted, and `scripts/hawk-token` inside a box (detected by `/etc/agentbox-identity`) reads the one JWT from the socket. Without it boxed sessions silently answered with Gemini.

- **`Dockerfile`** — Ubuntu 24.04 with docker-ce (the box runs its own `dockerd`), Chrome (omp's browser tool), pip, postgresql-client, sqlite3, socat, wget, passwordless sudo for uid 1000, and the `/etc/agentbox-identity` marker. Other tools are not installed here: the host's mise store is mounted in, so `mise.toml` pins apply inside exactly as on the host.
- **`entrypoint.sh`** — runs as root: starts the inner dockerd (its image store is an anonymous volume that dies with the box), inserts the DOCKER-USER rule against the metadata forwarder, logs the inner docker into ghcr.io as the session user with the agent-tier `GHCR_PULL_TOKEN`, then `setpriv`s to `AGENTBOX_USER` for the session command.

Root inside the box is Sysbox fake-root (a user namespace on the host), so `sudo apt-get install`, the inner Docker daemon, and nested containers (`tl run` sandboxes) work without host privileges. `sudo apt` installs die with the box; mise/uv/bun/npm installs persist through the mounted stores; durable image changes go in the Dockerfile plus `agentbox rebuild`.

knives: claims are keyed by the omp session id, so a boxed `knives start` is released at box exit without `--force`. The registered forks still live at their pre-`~/src` paths (`~/inspect/*`, `~/toon-rust/default`, …), which a box does not mount; `knives start` from a box works once a fork's checkout is moved under `~/src` (knives places workspaces next to the checkout, so they land inside the mount).

Known gap until `sjawhar/forward` §9 lands: human-tier `secrets` from a box is rejected by the broker (`could not communicate with secretsd: Connection reset by peer`), and omp's own secretsd registration shows as deferred; agent-tier keys work. `agentbox doctor` reports the exact symptom as expected.

### How changes take effect

The image tag is the content hash of `Dockerfile` + `entrypoint.sh` (`agentbox:<12 hex>`), so editing either yields a new tag: the next `agentbox omp` builds it, and `agentbox rebuild` builds it now and lists running boxes still on an older tag — those move when their sessions restart. `agentbox doctor` health-checks the host side and a throwaway box (tools, absent identity dirs, App-only gh, secrets tiers, AWS, hawk-token, google-user-token, envoy, forward channel parity with the host, inner docker). `agentbox sh <box|session-id>` execs into a running session's box.

## Bare VM (dormant)

`config.toml` has two tables: `[devpod]` image/resources for the old devcontainer flow, and a `[devbox]` cloud-init `user_data` that provisions a bare VM (Tailscale, Docker, Chrome, VS Code, headless-screenshot fonts and tooling, apt packages, a signing key). Useful on its own.

- Connect Tailscale last in `user_data` so its login URL stays visible in the console buffer.
- The `ip rule ... priority 5200` line is load-bearing: without it, replies to VPC-sourced traffic go back out the Tailscale tunnel and TCP breaks. Don't drop it as dead code.
- Both `.gitconfig` and `.jjconfig.toml` enable signing unconditionally and *fail* to commit when `~/.ssh/jj-signing` is missing. `user_data` generates one and prints the pubkey to the console: add it to `jj-allowed-signers` and register it on GitHub as a Signing Key. The agent box gets the host's key mounted read-only instead, so commits inside a box carry the host's registered signature.
