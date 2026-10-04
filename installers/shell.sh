#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DOTFILES_SOURCE_LINE='[ ! -f "${HOME}/.dotfiles/.bashrc" ] || . "${HOME}/.dotfiles/.bashrc"'
if [ ! -f ~/.bashrc ]; then
    printf '%s\n\n' "$DOTFILES_SOURCE_LINE" > ~/.bashrc
elif [ "$(sed -n '1p' ~/.bashrc)" != "$DOTFILES_SOURCE_LINE" ]; then
    # Must be the FIRST line, not merely present anywhere: the stock
    # ~/.bashrc returns early for a non-interactive shell (`case $- in *i*)
    # ;; *) return;; esac`) before reaching anything after that guard, so a
    # source line left after it -- a hand-edit, or a machine whose ~/.bashrc
    # predates this installer's prepend step -- is invisible to exactly the
    # shells that most need PATH/mise/MISE_GITHUB_TOKEN: `ssh host cmd`,
    # cron, this installer's own non-interactive callers. The old check here
    # (`grep -qF` for mere presence) treated that wrong position as already
    # installed and never moved it, which is how devbox-agents-2 stayed
    # broken across every re-run (found 2026-10-05: `ssh devbox-agents-2
    # 'bash -lc "command -v mise"'` found nothing).
    { grep -vF '.dotfiles/.bashrc' ~/.bashrc || true; } > ~/.bashrc.tmp
    { printf '%s\n\n' "$DOTFILES_SOURCE_LINE"; cat ~/.bashrc.tmp; } > ~/.bashrc.tmp2
    mv ~/.bashrc.tmp2 ~/.bashrc
    rm -f ~/.bashrc.tmp
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
