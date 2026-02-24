#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib.sh"

LAPTOP_DIR="${DOTFILES_DIR}/laptop"

# --- Aerospace-style workspace switching ---
chmod +x "${LAPTOP_DIR}/aerospace-ws"
ensure_link "${LAPTOP_DIR}/aerospace-ws" ~/.local/bin/aerospace-ws

# --- PipeWire: default webcam to 1080p30 instead of 480p25 ---
mkdir -p ~/.config/pipewire/pipewire.conf.d
ensure_link "${DOTFILES_DIR}/pipewire/10-video-quality.conf" ~/.config/pipewire/pipewire.conf.d/10-video-quality.conf

# --- COSMIC custom shortcuts ---
mkdir -p ~/.config/cosmic/com.system76.CosmicSettings.Shortcuts/v1
ensure_link "${LAPTOP_DIR}/shortcuts-custom" \
    ~/.config/cosmic/com.system76.CosmicSettings.Shortcuts/v1/custom
