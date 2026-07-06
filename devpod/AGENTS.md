# devpod

DevPod container setup for the remote development machine.

## Key files

- **`Dockerfile`** — dev image; pins tool versions via `ARG` (docker, buildx, helm, k9s, kubectl, ...).
- **`config.toml`** — devpod/devbox provisioning: image name, resources, and a cloud-init `user_data` block that installs Tailscale, Docker, and other apt packages.
- **`entrypoint.sh`** — container startup: brings up `tailscaled` (userspace networking) with a SOCKS5 proxy on `:1055` and HTTP proxy on `:1080`, then execs `sshd`.
- **`proxy.sh`** — sourceable helper exporting `HTTP_PROXY`/`ALL_PROXY`/`NO_PROXY` to route traffic through the Tailscale proxies.

## Conventions

- Pin tool versions with `ARG` in the `Dockerfile` rather than floating tags.
- Keep the proxy ports in `entrypoint.sh` and `proxy.sh` in sync (`:1055` SOCKS5, `:1080` HTTP).

## How changes take effect

Changes apply when the image is rebuilt and the devpod is (re)provisioned. `config.toml` `user_data` runs on instance creation; `entrypoint.sh` runs on container start.
