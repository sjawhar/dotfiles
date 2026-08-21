#!/bin/bash
set -euo pipefail

DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

usage() {
    echo "Usage: $(basename "$0") {serve|daemon}" >&2
}

MISE="${DOTFILES_DIR}/bin/mise"

case "${1:-}" in
    serve)
        units=(forward-serve.service)
        config_source=config-serve.toml
        ;;
    daemon)
        units=(forward-daemon.service omp-browser-relay.service)
        config_source=config.toml
        "$MISE" which omp >/dev/null 2>&1 || "$MISE" install "github:sjawhar/oh-my-pi@latest"
        ;;
    *)
        usage
        exit 1
        ;;
esac

if ! "$MISE" which forward >/dev/null 2>&1; then
    "$MISE" install forward@latest
fi

mkdir -p "${HOME}/.config/systemd/user"
mkdir -p "${HOME}/.config/forward"
# The two roles run on different machines, so both use the same well-known
# config path and neither wrapper nor unit ExecStart has to know its role.
ln -sfn "${DOTFILES_DIR}/forward/${config_source}" "${HOME}/.config/forward/config.toml"
for unit in "${units[@]}"; do
    ln -sfn "${DOTFILES_DIR}/forward/${unit}" "${HOME}/.config/systemd/user/${unit}"
done

service="${units[0]%.service}"
systemctl --user daemon-reload 2>/dev/null \
    || echo "NOTE: could not reload ${service} (no user systemd session here?) — reload it on the target machine."

for unit in "${units[@]}"; do
    service="${unit%.service}"
    systemctl --user enable --now "$service" 2>/dev/null \
        || echo "NOTE: could not enable ${service} (no user systemd session here?) — enable it on the target machine."
    systemctl --user try-restart "$service" 2>/dev/null \
        || echo "NOTE: could not restart ${service} (no user systemd session here?) — restart it on the target machine."
done

if [ "${1:-}" = daemon ]; then
    "$MISE" exec "github:sjawhar/oh-my-pi" -- omp browser-relay install
    echo "Chrome (manual, once): chrome://extensions -> enable Developer mode -> Load unpacked -> ~/.omp/browser-relay/extension"
    echo "Load the unpacked extension ONLY. Ignore any 'omp config set browser.relay true' line above:"
    echo "dotfiles supplies browser.relayUrl; browser.relay intentionally stays false (agents opt in per call with app.relay)."
fi
