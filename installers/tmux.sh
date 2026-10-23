#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if ! command -v tmux &>/dev/null; then
    sudo apt-get install -y -qq tmux >/dev/null
fi

ensure_link "${DOTFILES_DIR}/.tmux.conf" ~/.tmux.conf

if command -v tmux &>/dev/null; then
    ensure_clone https://github.com/tmux-plugins/tpm "${HOME}/.tmux/plugins/tpm"
    # TPM only *sources* `*.tmux` from each @plugin's directory (its
    # scripts/source_plugins.sh); installing them is a key press it never gets
    # on an unattended machine. Cloning them here is what that key press does,
    # and without it `set -g @plugin` is silently inert: this machine ran for
    # nine days with no tmux-resurrect, so nothing was ever saved and the
    # restore on server start had no file to read (2026-10-23).
    ensure_clone https://github.com/tmux-plugins/tmux-sensible "${HOME}/.tmux/plugins/tmux-sensible"
    ensure_clone https://github.com/tmux-plugins/tmux-resurrect "${HOME}/.tmux/plugins/tmux-resurrect"
    ensure_clone https://github.com/tmux-plugins/tmux-continuum "${HOME}/.tmux/plugins/tmux-continuum"
fi

# The attention cockpit (scripts/tmux-attention) watches its queue directory.
ensure_command inotifywait "sudo apt-get install -y -qq inotify-tools >/dev/null"
