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
        # Every devbox has its own pairing: forward/config-serve-<host>.toml and
        # the laptop's forward/config-daemon-<host>.toml. A host without one is
        # not paired, and installing another host's addresses would mint its
        # preview URLs, so there is no default.
        host="$(hostname -s)"
        config_source="config-serve-${host}.toml"
        if [ ! -f "${DOTFILES_DIR}/forward/${config_source}" ]; then
            echo "forward.sh serve: this machine (${host}) has no forward pair; add forward/config-serve-${host}.toml and forward/config-daemon-${host}.toml" >&2
            exit 1
        fi
        ;;
    daemon)
        # The laptop's daemons are the per-pair forward-daemon@<pair> units below.
        units=(omp-browser-relay.service)
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
# forward-serve reads ~/.config/forward/config.toml, so the devbox role links its
# pair's file there. Laptop daemons name their pair's file with --config instead
# (below), and the laptop has no config.toml.
if [ "${1:-}" = serve ]; then
    ln -sfn "${DOTFILES_DIR}/forward/${config_source}" "${HOME}/.config/forward/config.toml"
fi
# Earlier releases wrote an ambient relay endpoint; remove it wherever it
# still exists so no machine keeps a bypass-era configuration.
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
rm -f "${config_home}/omp/browser-relay.yml" "${config_home}/environment.d/browser-relay.conf"
for unit in "${units[@]}"; do
    ln -sfn "${DOTFILES_DIR}/forward/${unit}" "${HOME}/.config/systemd/user/${unit}"
done

services=()
for unit in "${units[@]}"; do
    services+=("${unit%.service}")
done
# forward trusts one peer per daemon, so the laptop runs one daemon for each
# devbox: forward/config-daemon-<pair>.toml is linked to
# ~/.config/forward/config-<pair>.toml and read by forward-daemon@<pair>.
if [ "${1:-}" = daemon ]; then
    # Earlier installs ran a default pair (forward-daemon.service, linked
    # config.toml) beside the named ones; remove it wherever it still exists.
    systemctl --user disable --now forward-daemon.service 2>/dev/null || true
    rm -f "${HOME}/.config/systemd/user/forward-daemon.service" "${HOME}/.config/forward/config.toml"
    systemctl --user reset-failed forward-daemon.service 2>/dev/null || true
    for pair_config in "${DOTFILES_DIR}"/forward/config-daemon-*.toml; do
        [ -e "$pair_config" ] || continue
        pair="${pair_config##*/config-daemon-}"
        pair="${pair%.toml}"
        ln -sfn "$pair_config" "${HOME}/.config/forward/config-${pair}.toml"
        # A pair sharing an address with another moves its URL channel with a
        # drop-in, since that port is a flag, not a config key.
        if [ -d "${DOTFILES_DIR}/forward/forward-daemon@${pair}.service.d" ]; then
            ln -sfn "${DOTFILES_DIR}/forward/forward-daemon@${pair}.service.d" \
                "${HOME}/.config/systemd/user/forward-daemon@${pair}.service.d"
        fi
        services+=("forward-daemon@${pair}")
        # A pair on loopback is carried over an SSH tunnel: forward-tunnel@.service
        # holds it open (Host <pair>-tunnel in ssh config), signed in with its own
        # key, which connects only once its public half is in the devbox's
        # authorized_keys.
        if grep -qE '^listen = "127\.' "$pair_config"; then
            services+=("forward-tunnel@${pair}")
            if [ ! -f "${HOME}/.ssh/forward-tunnel" ]; then
                mkdir -p -m 700 "${HOME}/.ssh"
                ssh-keygen -q -t ed25519 -N '' -C "forward-tunnel@$(hostname -s)" -f "${HOME}/.ssh/forward-tunnel"
                echo "Created ~/.ssh/forward-tunnel; add ~/.ssh/forward-tunnel.pub to the authorized_keys of each tunnelled devbox."
            fi
        fi
    done
    ln -sfn "${DOTFILES_DIR}/forward/forward-daemon@.service" "${HOME}/.config/systemd/user/forward-daemon@.service"
    ln -sfn "${DOTFILES_DIR}/forward/forward-tunnel@.service" "${HOME}/.config/systemd/user/forward-tunnel@.service"
fi
# The devbox side of such a pair still dials the laptop's default URL channel
# port; its host redirects that to the pair's port (a system unit: iptables).
if [ "${1:-}" = serve ] && [ -f "${DOTFILES_DIR}/forward/channel-nat-${host}.service" ]; then
    sudo systemctl enable --now "${DOTFILES_DIR}/forward/channel-nat-${host}.service"
fi

systemctl --user daemon-reload 2>/dev/null \
    || echo "NOTE: could not reload ${services[0]} (no user systemd session here?) — reload it on the target machine."

for service in "${services[@]}"; do
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
    echo "Browser access is per-session: forward browser grant --ttl 30m, then pass the printed URL as app.cdp_url. browser.relay intentionally stays false."
    echo "The grant runs secretsd's YubiKey ceremony (capability 'browser', key CAP_BROWSER); no token setup is needed."
fi
