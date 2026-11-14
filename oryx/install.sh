#!/bin/bash
# oryx's entry point: arms the dotfiles-follow timer, with oryx's drop-in
# (oryx/dotfiles-follow.service.d/oryx.conf), so oryx's ~/.dotfiles follows
# origin's main. Run it once on oryx: bash ~/.dotfiles/oryx/install.sh
# It needs no Tailscale and runs no forward installer: laptop/oryx-tunnel-setup
# writes oryx's forward config.
set -euo pipefail
# shellcheck source=oryx/../installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib.sh"

if [[ "$(hostname -s)" != oryx ]]; then
    echo "oryx/install.sh: this installer runs only on oryx" >&2
    exit 1
fi

mkdir -p "$HOME/.config/systemd/user/dotfiles-follow.service.d"
ensure_link "$HOME/.dotfiles/oryx/dotfiles-follow.service.d/oryx.conf" \
    "$HOME/.config/systemd/user/dotfiles-follow.service.d/oryx.conf"
arm_user_timer devbox dotfiles-follow
