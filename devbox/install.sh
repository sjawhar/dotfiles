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

echo "--- Devbox setup complete ---"
