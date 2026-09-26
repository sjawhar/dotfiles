#!/bin/bash
# Devbox-specific setup (remote dev machine). Not sourced from the main install.sh.
set -euo pipefail
# shellcheck source=devbox/../installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib.sh"

DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DEVBOX_DIR="${DOTFILES_DIR}/devbox"

# --- YubiKey PC/SC channel: owned by forward (forward serve serves
# ~/.pcscd/pcscd.comm and relays to the laptop's forward daemon). The old
# SSH-tunnel socat bridge is retired on this machine; clean it up if present.
# (oryx still uses the tunnel pattern — see devbox/pcscd-bridge.service.)
mkdir -p ~/.pcscd ~/.config/systemd/user
if systemctl --user is-enabled pcscd-bridge.service &>/dev/null; then
    systemctl --user disable --now pcscd-bridge.service || true
fi
rm -f ~/.config/systemd/user/pcscd-bridge.service
systemctl --user daemon-reload

# Devbox serve role exposes files through the laptop tunnel without binding the laptop's forwarded port.
bash "${DOTFILES_DIR}/installers/forward.sh" serve

# One embedding server for every omp session on the box (see installers/omp-embed.sh):
# without it each session loads its own ~1 GB copy of the mnemopi embedding model.
bash "${DOTFILES_DIR}/installers/omp-embed.sh"

# Anonymous Docker volumes no container references piled up to 308 GB unnoticed on
# 2026-09-25 and took the shared disk to 91%; prune them every 6 h (see the unit files).
ensure_link "${DEVBOX_DIR}/docker-volume-prune.service" ~/.config/systemd/user/docker-volume-prune.service
ensure_link "${DEVBOX_DIR}/docker-volume-prune.timer"   ~/.config/systemd/user/docker-volume-prune.timer
systemctl --user daemon-reload
systemctl --user enable --now docker-volume-prune.timer

echo "--- Devbox setup complete ---"
