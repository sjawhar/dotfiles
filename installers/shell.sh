#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DOTFILES_SOURCE_LINE='[ ! -f "${HOME}/.dotfiles/.bashrc" ] || . "${HOME}/.dotfiles/.bashrc"'
if [ ! -f ~/.bashrc ]; then
    printf '%s\n\n' "$DOTFILES_SOURCE_LINE" > ~/.bashrc
elif ! grep -qF '.dotfiles/.bashrc' ~/.bashrc; then
    { printf '%s\n\n' "$DOTFILES_SOURCE_LINE"; cat ~/.bashrc; } > ~/.bashrc.tmp
    mv ~/.bashrc.tmp ~/.bashrc
fi
. "${DOTFILES_DIR}/.bashrc"

# Gitconfig
ensure_link "${DOTFILES_DIR}/.gitconfig" ~/.gitconfig


