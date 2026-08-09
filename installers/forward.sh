#!/bin/bash
set -euo pipefail

DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

usage() {
    echo "Usage: $(basename "$0") {serve|daemon}" >&2
}

case "${1:-}" in
    serve)
        service=forward-serve
        unit_source="${DOTFILES_DIR}/forward/forward-serve.service"
        ;;
    daemon)
        service=forward-daemon
        unit_source="${DOTFILES_DIR}/forward/forward-daemon.service"
        ;;
    *)
        usage
        exit 1
        ;;
esac

MISE="${DOTFILES_DIR}/bin/mise"
if ! "$MISE" which forward >/dev/null 2>&1; then
    "$MISE" install forward@latest
fi

mkdir -p "${HOME}/.config/systemd/user"
mkdir -p "${HOME}/.config/forward"
# The two roles run on different machines, so both use the same well-known
# config path and neither wrapper nor unit ExecStart has to know its role.
if [ "$service" = forward-daemon ]; then
    ln -sfn "${DOTFILES_DIR}/forward/config.toml" "${HOME}/.config/forward/config.toml"
else
    ln -sfn "${DOTFILES_DIR}/forward/config-serve.toml" "${HOME}/.config/forward/config.toml"
fi
ln -sfn "$unit_source" "${HOME}/.config/systemd/user/${service}.service"

systemctl --user daemon-reload 2>/dev/null \
    || echo "NOTE: could not reload ${service} (no user systemd session here?) — reload it on the target machine."
systemctl --user enable --now "$service" 2>/dev/null \
    || echo "NOTE: could not enable ${service} (no user systemd session here?) — enable it on the target machine."
systemctl --user try-restart "$service" 2>/dev/null \
    || echo "NOTE: could not restart ${service} (no user systemd session here?) — restart it on the target machine."
