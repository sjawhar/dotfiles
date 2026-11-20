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

# mise.toml cannot pin mise itself, so its version is pinned here. A machine that
# already has mise keeps its binary; this pins a fresh install.
MISE_VERSION=v2026.10.11
ensure_command mise "curl -fsSL https://mise.run | MISE_VERSION=${MISE_VERSION} MISE_INSTALL_PATH=\"${DOTFILES_DIR}/bin/mise\" sh"

# mise's data dir, absolute as scripts/dotfiles-follow makes it: the install goes there, and its
# .dotfiles-install.lock is the one the dotfiles' other installs take.
export MISE_DATA_DIR="${MISE_DATA_DIR:-$HOME/.mise}"
[[ "$MISE_DATA_DIR" = /* ]] || MISE_DATA_DIR="$PWD/$MISE_DATA_DIR"

if [ "$SKIP_MISE" = true ]; then
    echo "Skipping mise install (--skip-mise)"
else
    echo "Installing tools via mise..."
    # A directory it cannot make shows up as flock's failure to open the lock, a failed try.
    mkdir -p "$MISE_DATA_DIR" 2>/dev/null || true
    # Under the lock (a wait of 600 s for it is a failed try), with DOTFILES_INSTALL_LOCK_HELD=1 so
    # a knives call under the install does not wait on it; under a caller that holds the lock and
    # set the marker, without it.
    for _ in {1..3}; do
        if [ -n "${DOTFILES_INSTALL_LOCK_HELD:-}" ]; then
            mise install && break
        elif DOTFILES_INSTALL_LOCK_HELD=1 flock -w 600 "$MISE_DATA_DIR/.dotfiles-install.lock" mise install; then
            break
        fi
        echo "Some tools failed to install (may be rate-limited). Run 'mise install' later."
        sleep 1
    done
fi

eval "$(mise activate bash)"
