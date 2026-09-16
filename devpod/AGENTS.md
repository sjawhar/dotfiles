# devpod

Two independent halves: the opt-in **agent box image** (`Dockerfile`, `entrypoint.sh`, the `docker` and `jj` wrappers, `agentbox-dockerd`) that `scripts/agentbox` runs a session in, and a **cloud-init script** for a bare VM (`config.toml` `[devbox]` `user_data`), currently dormant.

## Agent box image

One disposable Sysbox container per **agent session**: `agentbox omp [DIR] [omp args…]` runs `shims/omp` as the container's main process, so the box lives exactly as long as the session and `--rm` cleans it up on exit or pane kill. The whole home directory is mounted read-write, minus a hidden list of the user's personal third-party logins (`~/.config/gh`, `~/.ssh` with the jj-signing pair re-exposed, `~/.docker`, `~/.gnupg`, keyrings, gcloud, codex, anthropic, opencode auth, the gws `personal`/`work` OAuth caches, pup and opencode-mcp OAuth files, `~/.config/google-chrome`) — each shadowed by an empty ubuntu-owned per-box directory under `/run/user/$UID/agentbox/<box>/`, bound over the target. The rule for the list: a personal login to a third party where an agent-tier equivalent exists (gh-app routing, `GHCR_PULL_TOKEN`, provider keys via secretsd). A session therefore behaves like a host session — checkouts, worktrees, omp state and memory, mise tools, secrets agent tier, AWS, forward, gws machine identity, envoy — while pushing to GitHub only as the App its repo routes to.

AWS is the exception by design (the instance role is meant for agents): `~/.aws` is visible and the box reaches the EC2 metadata service through a socat forwarder `scripts/agentbox` runs on the host as a transient user unit (`agentbox-imds`, bound to the box network's gateway, sources limited to that network); `agentbox-dockerd` drops container traffic to it inside the box so a sandbox the box starts cannot hold the role.

Hawk tokens (the anthropic provider in `omp/models.yml` resolves `!hawk-token`) are the second host-mediated credential: hawk keeps the user's Cognito session in the GNOME keyring, and the keyring stays out of the box — the keyrings directory is hidden and neither the session bus nor the keyring control socket is mounted, since the same keyring holds the user's gh login. Instead `scripts/agentbox` runs a second transient user unit on the host, `agentbox-hawk-token`: socat listens on `/run/user/$UID/agentbox/hawk-token/hawk-token.sock` (mode 600, `fork`) and runs `scripts/hawk-token` once per connection; the directory is mounted into the box, and `scripts/hawk-token` inside a box (detected by `/etc/agentbox-identity`) reads the one JWT from that socket instead of running hawk. Any process in a box can mint exactly as any host agent session can; that is the intended posture, not a leak. The unit is recreated on the next launch after a reboot or `systemctl --user stop`, restarts on its own if socat dies, and `agentbox doctor` checks it on both sides.

- **`Dockerfile`** — Ubuntu 24.04 with docker-ce (the box runs its own `dockerd`), Chrome, pip, postgresql-client, sqlite3, socat, wget, passwordless sudo for uid 1000, and a `/etc/agentbox-identity` marker. Other tools are not installed here: the host's mise store is mounted in, so `mise.toml` pins apply inside exactly as on the host.
- **`entrypoint.sh`** — validates `AGENTBOX_IMDS_FORWARDER` and execs the session command. It no longer starts dockerd.
- **`docker`** (→ `/usr/local/bin/docker`) — starts the inner dockerd **lazily** on the first `docker` call (flock + `docker info` re-check, so concurrent first calls both succeed), logs into ghcr.io as ubuntu with the agent-tier `GHCR_PULL_TOKEN`, and records the session→volume mapping to `~/.local/state/agentbox/sessions/<session>` so `--resume`/`-c` reattach the same `/var/lib/docker` volume. An idle box runs no dockerd.
- **`agentbox-dockerd`** (→ `/usr/local/libexec/agentbox-dockerd`, via sudo) — `setsid dockerd`, waits for readiness, inserts the DOCKER-USER rule against the IMDS forwarder, opens the socket.
- **`jj`** (→ `/usr/local/bin/jj`) — records every successful `jj workspace add` to `~/.local/state/agentbox/sessions/<session>.workspaces` (plus a box→session breadcrumb) so the launcher can clean workspaces up when the box exits and recreate them on `--resume`; `knives start` goes through it too. A row is `main repo root, name, path, identity` (tab-separated; identity = inode and birth time of the workspace's `.jj`), and the launcher's close phase appends the change id @ sat on and the knives branch. The identity is what lets close remove a directory safely: a workspace another session later created at the same path no longer matches and is left in place. Everything else execs the real jj, resolved from the rest of PATH.

Root inside the box is Sysbox fake-root (a user namespace on the host), so `sudo apt-get install`, the inner Docker daemon, and nested containers (`tl run` sandboxes) work without host privileges. `sudo apt` installs die with the box; mise/uv/bun/npm installs persist via the mounted home; durable image changes go in the Dockerfile plus `agentbox rebuild`.

Known gap until `sjawhar/forward` §9 lands: human-tier `secrets` from a box is rejected by the broker (`could not communicate with secretsd: Connection reset by peer`); `agentbox doctor` reports that exact symptom as expected.

### How changes take effect

The image tag is the content hash of `Dockerfile` + `entrypoint.sh` + `docker` + `jj` + `agentbox-dockerd` (`agentbox:<12 hex>`), so editing any of them yields a new tag: the next `agentbox omp` builds it, and `agentbox rebuild` builds it now and lists running boxes still on an older tag — those move when their sessions restart. `agentbox doctor [BOX]` health-checks the host side and a throwaway (or named) box; `agentbox gc` reaps session volumes, state records, and per-box hide dirs.


## Bare VM (dormant)

`config.toml` has two tables: `[devpod]` image/resources for the old devcontainer flow, and a `[devbox]` cloud-init `user_data` that provisions a bare VM (Tailscale, Docker, Chrome, VS Code, headless-screenshot fonts and tooling, apt packages, a signing key). Useful on its own.

- Connect Tailscale last in `user_data` so its login URL stays visible in the console buffer.
- The `ip rule ... priority 5200` line is load-bearing: without it, replies to VPC-sourced traffic go back out the Tailscale tunnel and TCP breaks. Don't drop it as dead code.
- Both `.gitconfig` and `.jjconfig.toml` enable signing unconditionally and *fail* to commit when `~/.ssh/jj-signing` is missing. `user_data` generates one and prints the pubkey to the console: add it to `jj-allowed-signers` and register it on GitHub as a Signing Key. The agent box gets the host's key mounted read-only instead, so commits inside a box carry the host's registered signature.
