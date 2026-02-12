#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

mkdir -p ~/.config/nvim
ensure_link "${DOTFILES_DIR}/nvim/init.lua" ~/.config/nvim/init.lua

if command -v nvim &>/dev/null; then
    echo "Installing nvim plugins..."
    nvim --headless "+Lazy! sync" +qa || echo "NVIM PLUGIN INSTALL FAILED"
fi
