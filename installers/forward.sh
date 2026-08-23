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
# Earlier releases wrote an ambient relay endpoint; remove it wherever it
# still exists so no machine keeps a bypass-era configuration.
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
rm -f "${config_home}/omp/browser-relay.yml" "${config_home}/environment.d/browser-relay.conf"
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
    # A Flatpak Chrome cannot see ~/.omp by default, so "Load unpacked" below
    # would not even be able to open the directory. Grant read-only access to
    # exactly that path; the sandbox already shares the network namespace, so
    # the extension can still reach the relay on host loopback.
    if command -v flatpak >/dev/null 2>&1 \
        && flatpak info com.google.Chrome >/dev/null 2>&1; then
        flatpak override --user \
            --filesystem="${HOME}/.omp/browser-relay/extension:ro" \
            com.google.Chrome \
            && echo "Granted Flatpak Chrome read-only access to the extension directory."
    fi
    echo "Chrome (manual, once): chrome://extensions -> enable Developer mode -> Load unpacked -> ~/.omp/browser-relay/extension"
    echo "Load the unpacked extension ONLY. If the output above told you to run 'omp config set browser.relay true', ignore it."
    echo "First-time token setup (run from the devbox): ssh sami@sami forward browser init-token | secrets edit-human FORWARD_BROWSER_GRANT"
    echo "Browser access is per-session: secrets FORWARD_BROWSER_GRANT -- forward browser grant --ttl 30m, then pass the printed URL as app.cdp_url. browser.relay intentionally stays false."
fi
