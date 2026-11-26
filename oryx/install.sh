#!/bin/bash
# oryx's entry point: its op step links the oryx-only mise pin and installs the
# pinned 1Password CLI. `bash ~/.dotfiles/oryx/install.sh op` runs that step
# alone, with no sudo, systemctl or loginctl. A full run executes it first, then
# arms the dotfiles-follow timer, with oryx's drop-in
# (oryx/dotfiles-follow.service.d/oryx.conf), so oryx's ~/.dotfiles follows
# origin's main, and keeps what the A15 agent phone line needs. Run it once on
# oryx, as agent (the sudo user): bash ~/.dotfiles/oryx/install.sh
# It needs no Tailscale and runs no forward installer: oryx-edge-cutover writes
# oryx's forward configuration.
set -euo pipefail
# shellcheck source=oryx/../installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib.sh"

if [[ $# -eq 0 ]]; then
    run_all=true
elif [[ $# -eq 1 && $1 == op ]]; then
    run_all=false
else
    echo "usage: bash oryx/install.sh [op]" >&2
    exit 2
fi

if [[ "$(hostname -s)" != oryx ]]; then
    echo "oryx/install.sh: this installer runs only on oryx" >&2
    exit 1
fi

install_op() {
    # Absolute before cd, so the lock and mise process use the invoking directory's data dir.
    export MISE_DATA_DIR="${MISE_DATA_DIR:-$HOME/.mise}"
    [[ "$MISE_DATA_DIR" = /* ]] || MISE_DATA_DIR="$PWD/$MISE_DATA_DIR"

    mkdir -p "$HOME/.config/mise/conf.d"
    ensure_link "$HOME/.dotfiles/oryx/mise-op.toml" "$HOME/.config/mise/conf.d/op.toml"
    mkdir -p "$MISE_DATA_DIR"

    local lock="$MISE_DATA_DIR/.dotfiles-install.lock" rc=0
    # From $HOME, with the configuration directory this step links into and no
    # MISE_GLOBAL_CONFIG_FILE, so mise reads conf.d/op.toml rather than a checkout's own mise.toml:
    # without conf.d, `mise install op` installs op@latest and still exits 0. Under the install
    # lock that installers/mise.sh takes, or under a caller that holds it and set the marker.
    (
        cd "$HOME" || exit 1  # set -e does not hold in a subshell left of ||
        unset MISE_GLOBAL_CONFIG_FILE
        export MISE_CONFIG_DIR="$HOME/.config/mise"
        # mise's own 75 becomes 1, so 75 means only that the 600 s wait ran out.
        # shellcheck disable=SC2016  # the inner bash expands $? and $rc; this shell must not
        install='rc=0; mise install op || rc=$?; [ "$rc" != 75 ] || rc=1; exit "$rc"'
        if [ -n "${DOTFILES_INSTALL_LOCK_HELD:-}" ]; then
            bash -c "$install"
        else
            DOTFILES_INSTALL_LOCK_HELD=1 flock -w 600 -E 75 "$lock" bash -c "$install"
        fi
    ) || rc=$?

    if [ "$rc" = 75 ]; then
        echo "oryx/install.sh: another install held $lock for 600 s; op not installed" >&2
    fi
    [ "$rc" = 0 ] || return "$rc"
    echo "oryx/install.sh: op: linked ~/.config/mise/conf.d/op.toml and installed the pinned op"
}

install_op

if "$run_all"; then
    mkdir -p "$HOME/.config/systemd/user/dotfiles-follow.service.d"
    ensure_link "$HOME/.dotfiles/oryx/dotfiles-follow.service.d/oryx.conf" \
        "$HOME/.config/systemd/user/dotfiles-follow.service.d/oryx.conf"
    arm_user_timer devbox dotfiles-follow

    # The agent phone line (~/src/voice-clone/calls/hfp on sami's account): the Galaxy A15 is on
    # oryx's USB and pairs with oryx as a Bluetooth hands-free unit, so bluetoothd must run, and
    # sami's user manager (PipeWire, WirePlumber, the bridge) must outlive his ssh sessions.
    if ! systemctl is-enabled --quiet bluetooth || ! systemctl is-active --quiet bluetooth; then
        sudo systemctl enable --now bluetooth
    fi
    if [[ "$(loginctl show-user sami -p Linger --value 2>/dev/null)" != yes ]]; then
        sudo loginctl enable-linger sami
    fi
fi
