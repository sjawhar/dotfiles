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

# The always-safe prune (see devbox-prune.service): anonymous host volumes piled up to
# 308 GB on 2026-09-25, and agent boxes' own dangling images and build cache plus the Go
# build cache regrew ~585 GB in a day by 2026-09-27. Every 6 h.
# It was docker-volume-prune until it grew past volumes (AGENTC-1011); retire those units
# so nothing runs twice.
old_prune=~/.config/systemd/user/docker-volume-prune
if [[ -e "$old_prune.timer" || -L "$old_prune.timer" ]]; then
    systemctl --user disable --now docker-volume-prune.timer
fi
rm -f "$old_prune.service" "$old_prune.timer"
ensure_link "${DEVBOX_DIR}/devbox-prune.service" ~/.config/systemd/user/devbox-prune.service
ensure_link "${DEVBOX_DIR}/devbox-prune.timer"   ~/.config/systemd/user/devbox-prune.timer
systemctl --user daemon-reload
systemctl --user enable --now devbox-prune.timer

echo "--- Devbox setup complete ---"
