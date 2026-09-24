# devpod

A **cloud-init script** for a bare VM (`config.toml` `[devbox]` `user_data`), currently dormant, and the resource table of the old devcontainer flow. The agent box that `scripts/agentbox` runs sessions in lives in `agentbox/`.

## Bare VM (dormant)

`config.toml` has two tables: `[devpod]` image/resources for the old devcontainer flow, and a `[devbox]` cloud-init `user_data` that provisions a bare VM (Tailscale, Docker, Chrome, VS Code, headless-screenshot fonts and tooling, apt packages, a signing key). Useful on its own.

- Connect Tailscale last in `user_data` so its login URL stays visible in the console buffer.
- The `ip rule ... priority 5200` line is load-bearing: without it, replies to VPC-sourced traffic go back out the Tailscale tunnel and TCP breaks. Don't drop it as dead code.
- Both `.gitconfig` and `.jjconfig.toml` enable signing unconditionally and *fail* to commit when `~/.ssh/jj-signing` is missing. `user_data` generates one and prints the pubkey to the console: add it to `jj-allowed-signers` and register it on GitHub as a Signing Key. The agent box gets the host's key mounted read-only instead, so commits inside a box carry the host's registered signature.
