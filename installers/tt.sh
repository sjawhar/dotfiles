#!/bin/bash
# Set up the tt COSMIC window/idle watcher service.
#
# Standalone installer (like voxtype.sh / ghostty.sh) — not sourced by
# install.sh, run it on hosts where you want the watcher daemon.
#
# Assumes mise.sh has already installed both `tt` and `tt-watcher` aliases
# (configured in mise.toml). Because mise's GitHub backend currently dedups
# install jobs by (backend, version) regardless of alias (jdx/mise#9074),
# the bare `mise install` from mise.sh installs only the first alias seen.
# This script explicitly installs the watcher alias to work around that.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# --- Ensure tt-watcher binary is installed via mise ---
# Workaround for mise dedup bug: explicit alias install when bare `mise install` skips it.
if ! mise ls tt-watcher 2>/dev/null | grep -q '^tt-watcher'; then
    echo "Installing tt-watcher via mise (alias install — works around jdx/mise#9074)..."
    mise install tt-watcher@latest
fi

# --- Symlinks ---
mkdir -p ~/.config/systemd/user
ensure_link "${DOTFILES_DIR}/tt/tt-watcher.service" ~/.config/systemd/user/tt-watcher.service

# --- Service ---
systemctl --user daemon-reload
systemctl --user enable --now tt-watcher 2>/dev/null || true
