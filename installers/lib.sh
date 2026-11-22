#!/bin/bash
# Shared helpers for dotfiles installers. An installer uses them rather than its own link, clone,
# JSON edit, .deb install or systemd unit: each is safe to run twice, and the comments above them
# name the refusals and checks a hand-made step would lack.

DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

export PATH="${DOTFILES_DIR}/bin:${HOME}/.local/bin:$PATH"

# Links $2 to $1. A plain file at $2 is replaced only when it is identical to $1:
# an app that saves by renaming a temp file over the path (COSMIC's cosmic-config
# does) turns the link into a plain file, and one that differs holds a setting
# changed in that app. That, or a real directory at $2, fails the install, naming
# both paths, so nothing there is overwritten or has a link created inside it.
ensure_link() {
    if [[ -d $2 && ! -L $2 ]]; then
        echo "ensure_link: $2 is a directory, not a link to $1; move what it holds that you want into $1, remove it, then re-run" >&2
        return 1
    fi
    if [[ -L $2 && "$(readlink -- "$2")" == "$1" ]]; then
        return 0
    fi
    if [[ -e $2 && ! -L $2 ]] && ! cmp -s "$1" "$2"; then
        echo "ensure_link: $2 is a file that differs from $1; copy it over $1 to keep it or delete it, then re-run" >&2
        diff -u "$1" "$2" >&2 || true
        return 1
    fi
    ln -sfn "$1" "$2"
}

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
    # its dotfiles canonical.
    local real tmp
    real=$(readlink -f "$file")
    tmp=$(mktemp)
    jq "$transform" "$file" > "$tmp" && mv "$tmp" "$real"
}

# apt 2.8 can install a configured source's same-version copy instead of a local file
# (Debian bug #1058934), so apt gets only dependency fields and dpkg gets the checked file.
# It keeps the two refusals of apt that `dpkg -i` lacks: it installs nothing over a newer
# installed version or over a package held at another version. A hold at the pinned version is
# kept: `dpkg -i` sets the selection back to install, so the hold is set again after it, whether
# `dpkg -i` succeeds or fails; a failed re-hold says it could not set the hold again and returns
# non-zero, because the package may be left on install, where the next apt run can move it.
# Every step returns its own failure because `||` and `if` callers disable `set -e` inside it.
install_checked_deb() {
    local deb="$1" expected="$2"
    local actual package version installed want state installed_version held pre_depends depends rc
    local -a deps=()
    actual="$(sha256sum -- "$deb")" || return 1
    actual="${actual%% *}"
    if [[ "$actual" != "$expected" ]]; then
        echo "install_checked_deb: SHA-256 of $deb is $actual, expected $expected; installed nothing" >&2
        return 1
    fi
    package="$(dpkg-deb --field "$deb" Package)" || return 1
    version="$(dpkg-deb --field "$deb" Version)" || return 1
    # dpkg-query exits 1 for a package dpkg does not know (nothing is installed); any other
    # failure is an error.
    installed="$(dpkg-query -W -f='${db:Status-Want} ${db:Status-Status} ${Version}' "$package" 2>/dev/null)" || {
        rc=$?
        if ((rc != 1)); then
            echo "install_checked_deb: dpkg-query failed for $package (exit $rc); installed nothing" >&2
            return 1
        fi
        installed=""
    }
    read -r want state installed_version <<< "$installed"
    if [[ "$want" == hold && "$installed_version" != "$version" ]]; then
        # dpkg prints no version for a held package it has not installed, so name the state.
        if [[ -n "$installed_version" ]]; then held="at $installed_version"; else held="(${state//-/ })"; fi
        echo "install_checked_deb: $package is on hold $held; installed nothing (apt-mark unhold $package to install $version)" >&2
        return 1
    fi
    # apt counts neither `config-files` (only configuration files left) nor `not-installed` as
    # installed, so the version either state reports refuses nothing.
    if [[ "$state" == config-files || "$state" == not-installed ]]; then installed_version=""; fi
    if dpkg --compare-versions "$installed_version" gt "$version"; then
        echo "install_checked_deb: $package $installed_version is installed, newer than $version; installed nothing" >&2
        return 1
    fi
    pre_depends="$(dpkg-deb --field "$deb" Pre-Depends)" || return 1
    depends="$(dpkg-deb --field "$deb" Depends)" || return 1
    if [[ -n "$pre_depends" ]]; then deps+=("$pre_depends"); fi
    if [[ -n "$depends" ]]; then deps+=("$depends"); fi
    if ((${#deps[@]})); then
        DEBIAN_FRONTEND=noninteractive apt-get satisfy -y --no-install-recommends "${deps[@]}" || return 1
    fi
    rc=0
    DEBIAN_FRONTEND=noninteractive dpkg -i "$deb" || rc=$?
    if [[ "$want" == hold ]] && ! dpkg --set-selections <<< "$package hold"; then
        echo "install_checked_deb: could not set the hold on $package again; check apt-mark showhold and run apt-mark hold $package" >&2
        return "$((rc == 0 ? 1 : rc))"
    fi
    return "$rc"
}

# Give a box watch's service the session that owns THIS machine's box health, from
# BOX_HEALTH_OWNER in the installer's environment. The service units are committed and
# symlinked, so the per-machine value goes in a drop-in beside the link, never in the unit
# file. With the variable unset the drop-in is not written; an existing one is left alone and
# printed, because removing it would silently move a lane VM's alerts back to this box's role
# holder. Returns 1 on an unusable value.
ensure_box_health_owner() {
    # Two statements: bash expands every word of one `local` before it assigns any, so a $name in
    # the same statement is the caller's (unbound under `set -u`, which stopped every installer
    # that called this except omp-login-watch.sh, whose own $name happened to match).
    local name="$1"
    local dropin="$HOME/.config/systemd/user/$name.service.d/box-health-owner.conf"
    local owner="${BOX_HEALTH_OWNER:-}"
    if [ -z "$owner" ]; then
        [ -f "$dropin" ] && echo "$name: keeping $(grep -h BOX_HEALTH_OWNER "$dropin"); BOX_HEALTH_OWNER is unset here"
        return 0
    fi
    if ! [[ "$owner" =~ ^01[0-9a-f]{6}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
        echo "$name: BOX_HEALTH_OWNER=$owner is not an omp session id (01xxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)" >&2
        return 1
    fi
    mkdir -p "$(dirname "$dropin")" || return 1
    printf '[Service]\nEnvironment=BOX_HEALTH_OWNER=%s\n' "$owner" > "$dropin" || return 1
    echo "$name: alerts go to session $owner"
}

# user_unit_stopped_on_purpose <name>.<timer|service> [<unit-dir>]: true, after it says so, when
# someone stopped that systemd user unit on purpose (scripts/AGENTS.md, "Acting on the system"): it
# is not running, and this user manager has it but not enabled (`disabled`, or `linked` with
# nothing wanting it). `systemctl --user disable` also removes the link of a unit linked from
# outside the unit search path, as every unit arm_user_timer and arm_user_service link is (measured
# on systemd 255): a timer someone disabled then reads `not-found`, and the sign left is its
# service's link, still to the unit in <unit-dir>. A service someone disabled leaves no sign and
# reads as one never armed. A unit that is enabled, running or new was not stopped.
user_unit_stopped_on_purpose() {
    local unit="$1" units="${2:-}" state
    local name="${unit%.*}"
    local service="$HOME/.config/systemd/user/$name.service"
    systemctl --user is-active --quiet "$unit" && return 1
    state="$(systemctl --user is-enabled "$unit" 2>/dev/null)" || true
    case "$state" in
        disabled) ;;
        linked) state="linked but not enabled" ;;
        not-found)
            [[ "$unit" == *.timer && -n "$units" && -L "$service" && "$service" -ef "$units/$name.service" ]] || return 1
            state=disabled
            ;;
        *) return 1 ;;
    esac
    echo "$name: $unit is $state and not running, so someone stopped it on purpose; leaving it alone." \
        "systemctl --user enable --now ${units:+$units/}$unit arms it again."
}

# Arm the systemd user timer <name> whose units are <unit-dir>/<name>.service and
# <unit-dir>/<name>.timer in ~/.dotfiles: link both into ~/.config/systemd/user,
# enable and start the timer, and prove systemd scheduled it. The links come from
# ~/.dotfiles, not DOTFILES_DIR, so a unit armed from a workspace checkout does
# not point into a directory that will be removed. A timer someone stopped on purpose
# stays stopped, and the run says so (user_unit_stopped_on_purpose). Returns 1 on any
# refusal or failure; an installer keeps its own preconditions (its script, its tools).
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
    if user_unit_stopped_on_purpose "$name.timer" "$units"; then
        return 0
    fi
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

# Arm the systemd user SERVICE <name>, whose unit is <unit-dir>/<name>.service in ~/.dotfiles:
# link it into ~/.config/systemd/user, enable and start it, and prove it is running. For a
# watch that follows something (a journal, an inotify stream) rather than waking on a period,
# where a timer would be the wrong shape: the state it watches for can be unobservable by the
# time the next period comes round. Same refusals as arm_user_timer, for the same reasons, and a
# service someone stopped on purpose likewise stays stopped.
arm_user_service() {
    local dir="$1" name="$2" state
    local units="$HOME/.dotfiles/$dir"
    if [[ -f /.dockerenv ]] || [[ "$(systemd-detect-virt 2>/dev/null)" == docker ]]; then
        echo "$name: this is an agentbox; the host systemd cannot see its ~/.config/systemd." >&2
        echo "  Run $0 from a host shell instead." >&2
        return 1
    fi
    [[ -f "$units/$name.service" ]] || {
        echo "$name: $units/$name.service is missing; advance ~/.dotfiles to a main that has it" >&2
        return 1
    }
    if user_unit_stopped_on_purpose "$name.service" "$units"; then
        return 0
    fi
    mkdir -p "$HOME/.config/systemd/user" || return 1
    ensure_link "$units/$name.service" "$HOME/.config/systemd/user/$name.service" || return 1
    systemctl --user daemon-reload || return 1
    systemctl --user enable --now "$name.service" || return 1
    # Prove it is running rather than trusting `enable --now`: a Restart=always unit whose
    # ExecStart dies immediately reports success at enable time and is dead a second later.
    sleep 2
    state="$(systemctl --user is-active "$name.service" 2>/dev/null)" || true
    [[ "$state" == active ]] || {
        echo "$name: not running after start ($state); see systemctl --user status $name.service" >&2
        return 1
    }
    echo "$name: armed (running, $(systemctl --user show -p MainPID --value "$name.service"))"
}

# Disarm the systemd user timer <name> that arm_user_timer armed from <unit-dir> in ~/.dotfiles:
# disable and stop the timer (a run under way ends by itself), remove the timer's and the
# service's links from ~/.config/systemd/user, and reload. Only a link that points at this
# repository's unit is touched: a unit of the same name from anywhere else is not this one. A
# machine that never armed the timer changes nothing and prints nothing. Same refusal as
# arm_user_timer inside an agentbox, for the same reason. Returns 1 on any failure.
disarm_user_timer() {
    local dir="$1" name="$2" suffix link removed=0
    local links="$HOME/.config/systemd/user"
    if [[ -f /.dockerenv ]] || [[ "$(systemd-detect-virt 2>/dev/null)" == docker ]]; then
        echo "$name: this is an agentbox; the host systemd cannot see its ~/.config/systemd." >&2
        echo "  Run $0 from a host shell instead." >&2
        return 1
    fi
    link="$links/$name.timer"
    if [[ -L "$link" && "$(readlink -- "$link")" == "$HOME/.dotfiles/$dir/$name.timer" ]]; then
        echo "$name: timer was $(systemctl --user is-enabled "$name.timer" 2>&1 || true)/$(systemctl --user is-active "$name.timer" 2>&1 || true); disarming it"
        systemctl --user disable --now "$name.timer" || return 1
    fi
    for suffix in timer service; do
        link="$links/$name.$suffix"
        if [[ -L "$link" && "$(readlink -- "$link")" == "$HOME/.dotfiles/$dir/$name.$suffix" ]]; then
            rm -- "$link" || return 1
            echo "$name: removed $link"
            removed=1
        fi
    done
    if ((removed)); then
        systemctl --user daemon-reload || return 1
    fi
}
