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

ensure_command mise "curl -fsSL https://mise.run | MISE_INSTALL_PATH=\"${DOTFILES_DIR}/bin/mise\" sh"
mise trust ~/.config/mise/config.toml || echo "MISE TRUST FAILED"

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
