# devpod

Provisioning for the remote development machine, in two independent halves: a container image (`Dockerfile` + `entrypoint.sh`) and a cloud-init script for a bare VM (`config.toml` `[devbox]` `user_data`). Currently dormant — nothing here describes the machine these dotfiles usually run on.

## Key files

- **`Dockerfile`** — dev image; pins tool versions via `ARG` (docker, buildx, helm, k9s, kubectl, ...).
- **`config.toml`** — two things: `[devpod]` image/resources, and a `[devbox]` cloud-init `user_data` that provisions a bare VM (Tailscale, Docker, Chrome, VS Code, headless-screenshot fonts and tooling, apt packages, a signing key). Useful on its own even without the container.
- **`entrypoint.sh`** — container startup: brings up `tailscaled` (userspace networking) with a SOCKS5 proxy on `:1055` and HTTP proxy on `:1080`, then execs `sshd`.
- **`proxy.sh`** — sourceable helper exporting `HTTP_PROXY`/`ALL_PROXY`/`NO_PROXY` to route traffic through the Tailscale proxies.

## Conventions

- Pin tool versions with `ARG` in the `Dockerfile` rather than floating tags.
- Keep the proxy ports in `entrypoint.sh` and `proxy.sh` in sync (`:1055` SOCKS5, `:1080` HTTP).
- Tool versions appear in **two** places that must be updated together: `Dockerfile` `ARG`s and the binary installs in `config.toml` `user_data` (k9s, helm, kubectl).
- Connect Tailscale last in `user_data` so its login URL stays visible in the console buffer.
- The `ip rule ... priority 5200` line is load-bearing: without it, replies to VPC-sourced traffic go back out the Tailscale tunnel and TCP breaks. Don't drop it as dead code.

## How changes take effect

Changes apply when the image is rebuilt and the devpod is (re)provisioned. `config.toml` `user_data` runs on instance creation; `entrypoint.sh` runs on container start.

## Commit signing

Both `.gitconfig` and `.jjconfig.toml` enable signing unconditionally, and both tools *fail* to commit when `~/.ssh/jj-signing` is missing rather than committing unsigned. So anything provisioned here needs a key before it can commit:

- **VM** (`user_data`) — generates one and prints the pubkey to the console. Add it to `jj-allowed-signers` and register it on GitHub as a Signing Key.
- **Container** — gets no key, so `git commit` inside a freshly built image fails. Mount or forward an existing key rather than generating one per container: a generated key is unregistered on GitHub, so its commits would show Unverified.
