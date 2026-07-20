#!/bin/bash
# Devbox-specific setup (remote dev machine). Not sourced from the main install.sh.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib.sh"

DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DEVBOX_DIR="${DOTFILES_DIR}/devbox"

# --- pcscd bridge: YubiKey (on laptop) over SSH for sops human-tier secrets ---
# laptop/ssh-config RemoteForwards laptop pcscd to loopback TCP 12799; this
# service bridges it back to a user-owned unix socket that PC/SC clients
# (age-plugin-yubikey) find via PCSCLITE_CSOCK_NAME (set in .bashrc, keyed on
# the ~/.pcscd marker directory).
if ! dpkg -s socat &>/dev/null; then
    sudo apt-get install -y -qq socat >/dev/null
fi
mkdir -p ~/.pcscd ~/.config/systemd/user
ensure_link "${DEVBOX_DIR}/pcscd-bridge.service" ~/.config/systemd/user/pcscd-bridge.service
systemctl --user daemon-reload
systemctl --user enable --now pcscd-bridge.service

echo "--- Devbox setup complete ---"
