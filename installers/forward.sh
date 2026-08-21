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
        omp_config_source=config-serve.yml
        ;;
    daemon)
        units=(forward-daemon.service omp-browser-relay.service)
        config_source=config.toml
        omp_config_source=
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
if [ -n "$omp_config_source" ]; then
    omp_config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/omp"
    mkdir -p "$omp_config_dir"
    # OMP loads this only through PI_CONFIG_FILES in shims/omp, so the relay URL
    # is present only on the devbox serve role, never in shared config.yml.
    ln -sfn "${DOTFILES_DIR}/omp/${omp_config_source}" "${omp_config_dir}/browser-relay.yml"
fi
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
    echo "Load the unpacked extension ONLY. If the output above told you to run 'omp config set browser.relay true', ignore it."
    echo "dotfiles supplies browser.relayUrl; browser.relay intentionally stays false (agents opt in per call with app.relay)."
fi
