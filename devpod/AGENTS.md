# devpod

Dormant. `config.toml` holds `[devpod]`, the image and resources of the old devcontainer flow, and `[devbox]`, a cloud-init `user_data` that provisions a bare VM (Tailscale, Docker, Chrome, the dotfiles install, a signing key).

The comments in `user_data` say why each step is there, including the `ip rule` line that looks unused and is not; keep them with the code. The agent box is in `agentbox/`.
