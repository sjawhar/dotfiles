#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Docker Engine from Docker's own repo (docker-ce) via the official installer,
# never Ubuntu's docker.io: docker.io pulls `containerd`, which conflicts with
# the `containerd.io` of an already-installed docker-ce, so the apt install
# fails outright on a machine that has Docker already. It also ships neither the
# buildx nor the compose plugin.
if command -v apt-get &>/dev/null; then
    ensure_command docker "curl -fsSL https://get.docker.com | sudo sh"

    # Tooling that drives Docker over SSH — Pulumi's docker provider, the envoy
    # listener deploy — runs `docker` as the login user with no tty for sudo, so
    # socket access has to come from group membership. Skipped when the group is
    # absent, which is the socket-mounted devcontainer case: the CLI is real but
    # the engine is the host's.
    if getent group docker >/dev/null && ! id -nG "$USER" | grep -qw docker; then
        sudo usermod -aG docker "$USER"
        echo "NOTE: Added $USER to docker group. Start a new login session (or use 'sg docker') for it to apply."
    fi
fi
