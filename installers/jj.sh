#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ensure_link "${DOTFILES_DIR}/.jjconfig.toml" ~/.jjconfig.toml

# Watchman is required for fsmonitor (avoids full tree walk on every jj command).
# The Ubuntu apt package is a broken 2017 build (4.9.0) whose per-root watcher
# threads wedge under load (syncToNow cookie timeouts); install Meta's prebuilt
# release instead. Binaries hard-link their libs at /usr/local/lib.
WATCHMAN_VERSION="v2026.07.27.00"
install_watchman() {
    local tmp
    tmp="$(mktemp -d)"
    curl -fsSL -o "${tmp}/watchman.zip" \
        "https://github.com/facebook/watchman/releases/download/${WATCHMAN_VERSION}/watchman-${WATCHMAN_VERSION}-linux.zip"
    unzip -oq "${tmp}/watchman.zip" -d "$tmp"
    sudo cp "${tmp}/watchman-${WATCHMAN_VERSION}-linux/bin/"* /usr/local/bin/
    sudo cp "${tmp}/watchman-${WATCHMAN_VERSION}-linux/lib/"* /usr/local/lib/
    sudo chmod 755 /usr/local/bin/watchman /usr/local/bin/watchmanctl
    sudo mkdir -p /usr/local/var/run/watchman
    sudo chmod 2777 /usr/local/var/run/watchman
    rm -rf "$tmp"
}
ensure_command watchman install_watchman
# Replace the apt 4.9.0 build if it's what resolved
if watchman --version | grep -q '^4\.'; then
    watchman shutdown-server || true
    sudo apt-get remove -y watchman
    install_watchman
fi

mkdir -p ~/.config/jj
if [ ! -f ~/.config/jj/config.toml ]; then
    if [ -t 0 ]; then
        read -rp "Email for jj/git commits: " USER_EMAIL
        tmp="$(mktemp)"
        cat > "$tmp" <<EOF
[user]
name = "Sami Jawhar"
email = "${USER_EMAIL}"
EOF
        mv "$tmp" ~/.config/jj/config.toml
    else
        echo "Skipping jj config (non-interactive). Create ~/.config/jj/config.toml manually."
    fi
fi
