#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ensure_link "${DOTFILES_DIR}/.jjconfig.toml" ~/.jjconfig.toml

# Watchman is required for fsmonitor (avoids full tree walk on every jj command)
ensure_command watchman "sudo apt-get install -y watchman"

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
