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



# Ghostty's terminfo, for machines reached over ssh from the laptop: Ghostty
# sets TERM=xterm-ghostty, which ncurses does not know anywhere Ghostty itself
# is not installed, so tmux refuses to start ("missing or unsuitable
# terminal"). The source is checked in (ghostty/xterm-ghostty.terminfo), so no
# machine needs Ghostty or an infocmp donor; tic compiles it into ~/.terminfo.
if ! infocmp xterm-ghostty >/dev/null 2>&1 && command -v tic >/dev/null; then
    tic -x "${DOTFILES_DIR}/ghostty/xterm-ghostty.terminfo"
fi
