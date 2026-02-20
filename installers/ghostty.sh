#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# --- .deb package from mkasberg/ghostty-ubuntu ---
GHOSTTY_VERSION="1.2.3"
GHOSTTY_UBUNTU="24.04"

if ! command -v ghostty &>/dev/null; then
    echo "Installing Ghostty ${GHOSTTY_VERSION}..."
    tmp_deb="$(mktemp --suffix=.deb)"
    trap 'rm -f "$tmp_deb"' EXIT
    gh release download "${GHOSTTY_VERSION}-0-ppa1" \
        --repo mkasberg/ghostty-ubuntu \
        --pattern "ghostty_${GHOSTTY_VERSION}-0.ppa1_amd64_${GHOSTTY_UBUNTU}.deb" \
        --output "$tmp_deb"
    sudo dpkg -i "$tmp_deb" || sudo apt-get install -f -y
    rm -f "$tmp_deb"
    trap - EXIT
fi

# --- Config ---
mkdir -p ~/.config/ghostty
ensure_link "${DOTFILES_DIR}/ghostty/config" ~/.config/ghostty/config
