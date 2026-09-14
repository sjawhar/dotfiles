# devpod

Two independent halves: the opt-in **agent box image** (`Dockerfile` + `entrypoint.sh`) that `scripts/agentbox` runs a session in, and a **cloud-init script** for a bare VM (`config.toml` `[devbox]` `user_data`), currently dormant.

## Agent box image

An opt-in container for agent sessions that must not be able to act as the user on GitHub: rather than a policy in the shims, a container that simply lacks the user's GitHub and SSH credentials. AWS is the exception by design (the instance role is meant for agents): `~/.aws` is mounted read-write and the box reaches the EC2 metadata service through a socat forwarder `scripts/agentbox` runs on the host as a transient user unit (`agentbox-imds`, bound to the box network's gateway, sources limited to that network); `entrypoint.sh` drops container traffic to it inside the box so a sandbox the box starts cannot hold the role. `scripts/agentbox` starts one box per repository from this image under the Sysbox runtime (`sysbox-runc`), mounts an allow-list of the home directory (the repo and its `~/.worktrees/<repo>` workspaces, `~/.dotfiles`, mise's store, omp state, jj and gh-app routing config, the secretsd socket, the `core-context` and `core-ops` skill farms) and nothing else, and runs the same `shims/omp` inside. `omp` on the host is unchanged.

- **`Dockerfile`** — Ubuntu 24.04 with docker-ce (the box runs its own `dockerd`), `sudo` without password for uid 1000, and a `/etc/agentbox-identity` marker naming the environment. No tools are installed here: the host's mise store is mounted in, so `mise.toml` pins apply inside exactly as on the host.
- **`entrypoint.sh`** — starts the inner `dockerd`, prints `inner dockerd ready <version>` once it answers, and idles; sessions enter with `docker exec`. A daemon that does not come up exits 1 and the launcher removes the box.

Root inside the box is Sysbox fake-root (a user namespace on the host), so `sudo apt-get install`, the inner Docker daemon, and nested containers (`tl run` sandboxes) work without host privileges, and everything installed dies with `agentbox down`.

### How changes take effect

The image tag is the content hash of `Dockerfile` + `entrypoint.sh` (`agentbox:<12 hex>`), so editing either yields a new tag: `agentbox up` builds it on first use and `agentbox rebuild` builds it now and lists boxes still on an older tag. Boxes are disposable — `agentbox down <repo> && agentbox up <repo>` moves one to the new image; the repo, omp sessions, and caches live in mounts and survive.

## Bare VM (dormant)

`config.toml` has two tables: `[devpod]` image/resources for the old devcontainer flow, and a `[devbox]` cloud-init `user_data` that provisions a bare VM (Tailscale, Docker, Chrome, VS Code, headless-screenshot fonts and tooling, apt packages, a signing key). Useful on its own.

- Connect Tailscale last in `user_data` so its login URL stays visible in the console buffer.
- The `ip rule ... priority 5200` line is load-bearing: without it, replies to VPC-sourced traffic go back out the Tailscale tunnel and TCP breaks. Don't drop it as dead code.
- Both `.gitconfig` and `.jjconfig.toml` enable signing unconditionally and *fail* to commit when `~/.ssh/jj-signing` is missing. `user_data` generates one and prints the pubkey to the console: add it to `jj-allowed-signers` and register it on GitHub as a Signing Key. The agent box gets the host's key mounted read-only instead, so commits inside a box carry the host's registered signature.
