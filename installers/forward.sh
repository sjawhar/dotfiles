#!/bin/bash
# Installs forward on devboxes when executed directly; laptop/install.sh sources it for the binary.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

echo "Installing forward via mise..."
mise install forward@latest

install_forward_serve() {
    mkdir -p ~/.config/systemd/user
    ensure_link "${DOTFILES_DIR}/forward/forward-serve.service" ~/.config/systemd/user/forward-serve.service
    systemctl --user daemon-reload
    systemctl --user enable --now forward-serve
    systemctl --user try-restart forward-serve
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    install_forward_serve
fi
