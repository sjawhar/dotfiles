#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ensure_link "${DOTFILES_DIR}/.tmux.conf" ~/.tmux.conf

if command -v tmux &>/dev/null; then
    ensure_clone https://github.com/tmux-plugins/tpm "${HOME}/.tmux/plugins/tpm"
fi
