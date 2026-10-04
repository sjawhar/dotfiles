#!/bin/bash
# Shared helpers for dotfiles installers

DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

export PATH="${DOTFILES_DIR}/bin:${HOME}/.local/bin:$PATH"

ensure_link() { ln -sfn "$1" "$2"; }

ensure_clone() {
    local url="$1" dir="$2"
    [ -e "${dir}/.git" ] && return 0
    if [ -e "$dir" ]; then
        echo "Removing non-git directory: $dir" >&2
        rm -rf "$dir"
    fi
    mkdir -p "$(dirname "$dir")"
    git clone --depth 1 "$url" "$dir"
}

ensure_vendor() {
    local url="$1" name="$2" ref="${3:-}"
    local dir="${DOTFILES_DIR}/vendor/${name}"
    if [ ! -e "${dir}/.git" ]; then
        mkdir -p "$(dirname "$dir")"
        git clone --depth 1 "$url" "$dir"
    fi
    # origin follows the URL argument, so a vendor re-pointed at a fork fetches
    # the fork's commits on machines that cloned the original.
    git -C "$dir" remote set-url origin "$url"
    # Pinned vendors converge every machine on one commit; unpinned ones stay
    # at whatever HEAD they were cloned at (never auto-updated).
    if [ -n "$ref" ] && [ "$(git -C "$dir" rev-parse HEAD)" != "$ref" ]; then
        git -C "$dir" fetch --depth 1 origin "$ref"
        git -C "$dir" checkout --detach "$ref"
    fi
    if [ ! -e "${dir}/.jj" ] && command -v jj &>/dev/null; then
        ( cd "$dir" && jj git init --colocate )
    fi
}

ensure_command() {
    local name="$1" install_cmd="$2"
    local found
    found=$(command -v "$name" 2>/dev/null) || true
    # Skip shims — they wrap the real binary but don't mean it's installed
    if [ -n "$found" ] && [[ "$found" != "${DOTFILES_DIR}/shims/"* ]]; then
        return 0
    fi
    echo "Installing ${name}..."
    eval "$install_cmd"
    hash -r
    command -v "$name" &>/dev/null || { echo "${name} not found on PATH after install" >&2; return 1; }
}

ensure_json() {
    local file="$1" check="$2" transform="$3" desc="${4:-}"
    jq -e "$check" "$file" > /dev/null 2>&1 && return 0
    [ -n "$desc" ] && echo "$desc"
    # Write to the symlink target, not the symlink path: mv onto a symlink
    # replaces the link with a plain file, silently forking live config from
    # its dotfiles canonical (this destroyed the opencode.json link once).
    local real tmp
    real=$(readlink -f "$file")
    tmp=$(mktemp)
    jq "$transform" "$file" > "$tmp" && mv "$tmp" "$real"
}

# Arm the systemd user timer <name> whose units are <unit-dir>/<name>.service and
# <unit-dir>/<name>.timer in ~/.dotfiles: link both into ~/.config/systemd/user,
# enable and start the timer, and prove systemd scheduled it. The links come from
# ~/.dotfiles, not DOTFILES_DIR, so a unit armed from a workspace checkout does
# not point into a directory that will be removed. Returns 1 on any refusal or
# failure; an installer keeps its own preconditions (its script, its tools).
arm_user_timer() {
    local dir="$1" name="$2" suffix listed
    local units="$HOME/.dotfiles/$dir"
    # Must run on the host, not inside an agentbox. A box reaches the HOST's systemd
    # user manager over the relayed session bus, but its ~/.config/systemd is the
    # container's own -- so `systemctl enable` writes a unit the manager will never
    # load, while `systemctl cat` reads it back happily because that half is
    # client-side. The result looks armed and is not. Refuse instead.
    if [[ -f /.dockerenv ]] || [[ "$(systemd-detect-virt 2>/dev/null)" == docker ]]; then
        echo "$name: this is an agentbox; the host systemd cannot see its ~/.config/systemd." >&2
        echo "  Run $0 from a host shell instead." >&2
        return 1
    fi
    # Both units before either link: ~/.dotfiles advanced to a main that has the
    # watch's script but not yet its units would leave a dangling link behind a
    # failed `enable --now`.
    for suffix in service timer; do
        [[ -f "$units/$name.$suffix" ]] || {
            echo "$name: $units/$name.$suffix is missing; advance ~/.dotfiles to a main that has it" >&2
            return 1
        }
    done
    # Each step returns on its own failure: a caller that writes
    # `arm_user_timer … || exit 1` switches set -e off inside the function.
    mkdir -p "$HOME/.config/systemd/user" || return 1
    for suffix in service timer; do
        ensure_link "$units/$name.$suffix" "$HOME/.config/systemd/user/$name.$suffix" || return 1
    done
    systemctl --user daemon-reload || return 1
    systemctl --user enable --now "$name.timer" || return 1
    # Prove the timer is actually scheduled. `enable --now` on a timer whose unit
    # fails to load still exits 0 on some systemd versions, which would leave the
    # installer claiming an armed timer that never runs. Captured before the grep:
    # under pipefail, `grep -q` quitting at the row can SIGPIPE systemctl's footer
    # write and fail a timer that did register (seen 2026-09-30).
    listed="$(systemctl --user list-timers --all "$name.timer")" || return 1
    grep -q -- "$name" <<<"$listed" || {
        echo "$name: timer did not register; see systemctl --user status $name.timer" >&2
        return 1
    }
    echo "$name: armed ($(systemctl --user show -p NextElapseUSecRealtime --value "$name.timer"))"
}
