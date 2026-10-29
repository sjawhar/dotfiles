#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SKIP_MISE=false
for arg in "$@"; do
    case $arg in
        --skip-mise) SKIP_MISE=true ;;
    esac
done

mkdir -p ~/.config/mise
ensure_link "${DOTFILES_DIR}/mise.toml" ~/.config/mise/config.toml

# Make MISE_DATA_DIR (set in .bashrc) also visible to systemd --user services.
mkdir -p ~/.config/environment.d
ensure_link "${DOTFILES_DIR}/mise/mise.conf" ~/.config/environment.d/mise.conf
if command -v systemctl &>/dev/null && [ -n "${XDG_RUNTIME_DIR:-}" ]; then
    systemctl --user set-environment MISE_DATA_DIR="${HOME}/.mise" \
        || echo "WARNING: no reachable systemd --user manager; MISE_DATA_DIR applies at next login" >&2
fi

# mise itself is pinned here, since mise.toml cannot pin the tool that reads it. An install
# that finds another release at bin/mise (an older machine, a self-update) replaces it with
# this one; mise.run checks the download against the release's SHASUMS256.txt on GitHub.
MISE_PIN=2026.10.8
mise_found=$("${DOTFILES_DIR}/bin/mise" --version 2>/dev/null | cut -d' ' -f1) || true
if [ "$mise_found" != "$MISE_PIN" ]; then
    echo "Installing mise ${MISE_PIN} (found: ${mise_found:-none})..."
    curl -fsSL https://mise.run | MISE_VERSION="v${MISE_PIN}" MISE_INSTALL_PATH="${DOTFILES_DIR}/bin/mise" sh
    hash -r
fi

if [ "$SKIP_MISE" = true ]; then
    echo "Skipping mise install (--skip-mise)"
else
    echo "Installing tools via mise..."
    for _ in {1..3}; do
        mise install && break || echo "Some tools failed to install (may be rate-limited). Run 'mise install' later."
        sleep 1
    done
fi

eval "$(mise activate bash)"
