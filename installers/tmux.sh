#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if ! command -v tmux &>/dev/null; then
    sudo apt-get install -y -qq tmux >/dev/null
fi

ensure_link "${DOTFILES_DIR}/.tmux.conf" ~/.tmux.conf

if command -v tmux &>/dev/null; then
    ensure_clone https://github.com/tmux-plugins/tpm "${HOME}/.tmux/plugins/tpm"
fi

# The attention cockpit (scripts/tmux-attention) watches its queue directory.
ensure_command inotifywait "sudo apt-get install -y -qq inotify-tools >/dev/null"
