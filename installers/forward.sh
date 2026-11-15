#!/bin/bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() {
    echo "Usage: $(basename "$0") {serve|daemon}" >&2
}

fail() {
    echo "forward.sh ${1:-}: $2" >&2
    exit 1
}

# systemd 255's list-unit-files exits 1 and prints nothing, on stdout or stderr,
# when no unit file matches; a real failure prints its reason on stderr.
list_unit_files() {
    local errors status=0
    { errors="$(systemctl --user list-unit-files --no-legend --no-pager "$@" 2>&1 >&3 3>&-)" || status=$?; } 3>&1
    if (( status == 1 )) && [[ -z "$errors" ]]; then
        return 0
    fi
    [[ -z "$errors" ]] || printf '%s\n' "$errors" >&2
    return "$status"
}

# Print each unit name from the unit-file and unit-instance listings once, in
# their discovery order. A caller keeps responsibility for filtering the names.
find_units() {
    local unit_files
    local unit_instances
    local unit
    local rest
    local -A found=()

    if ! unit_files="$(list_unit_files "$@")"; then
        return 1
    fi
    if ! unit_instances="$(systemctl --user list-units --all --full --plain --no-legend --no-pager "$@")"; then
        return 2
    fi
    while read -r unit rest; do
        [[ -n "$unit" ]] || continue
        [[ -z "${found[$unit]+present}" ]] || continue
        found["$unit"]=1
        printf '%s\n' "$unit"
    done <<<"${unit_files}
${unit_instances}"
    return 0
}

# Oryx is not a Tailscale VM pair. Its dedicated forward-oryx service is
# installed after the ordinary daemon and browser-relay work below.
tunnel_pairs=(oryx)

is_tunnel_pair() {
    local pair
    for pair in "${tunnel_pairs[@]}"; do
        [[ "$1" = "$pair" ]] && return 0
    done
    return 1
}

role="${1:-}"
case "$role" in
    serve) units=(forward-serve.service) ;;
    daemon) units=(forward-daemon.service omp-browser-relay.service) ;;
    *) usage; exit 1 ;;
esac

MISE="${DOTFILES_DIR}/bin/mise"
config_home="$HOME/.config"

oryx_status() {
    printf 'forward.sh daemon: oryx: %s: %s\n' "$1" "$2"
}

oryx_fail() {
    printf 'forward.sh daemon: oryx: %s failed: %s\n' "$1" "$2" >&2
    exit 1
}

oryx_key() {
    local key="$HOME/.ssh/oryx-tunnel"
    local public_key="${key}.pub"
    local fingerprint
    local temporary
    local made=0

    if [[ ! -e "$key" ]]; then
        if ! mkdir -p "$HOME/.ssh"; then
            ORYX_ERROR="could not create $HOME/.ssh"
            return 1
        fi
        if ! ssh-keygen -q -t ed25519 -N "" -C "oryx-tunnel@$(hostname -s)" -f "$key"; then
            ORYX_ERROR="could not make $key"
            return 1
        fi
        made=1
    elif [[ ! -f "$key" ]]; then
        ORYX_ERROR="$key is not a regular file"
        return 1
    fi

    if [[ ! -f "$public_key" ]]; then
        temporary="${public_key}.tmp.$$"
        if ! ssh-keygen -y -f "$key" > "$temporary"; then
            rm -f -- "$temporary"
            ORYX_ERROR="could not derive $public_key"
            return 1
        fi
        if ! mv -f -- "$temporary" "$public_key"; then
            rm -f -- "$temporary"
            ORYX_ERROR="could not write $public_key"
            return 1
        fi
    fi
    if ! fingerprint="$(ssh-keygen -l -f "$public_key")"; then
        ORYX_ERROR="could not read the fingerprint of $public_key"
        return 1
    fi

    if (( made )); then
        oryx_status key "made $fingerprint"
    else
        oryx_status key "present $fingerprint"
    fi
}

oryx_enrol() {
    local public_key="$HOME/.ssh/oryx-tunnel.pub"

    # shellcheck disable=SC2088
    if ! ssh oryx '~/.local/bin/oryx-tunnel-enrol' < "$public_key"; then
        ORYX_ERROR="ssh oryx ~/.local/bin/oryx-tunnel-enrol failed; check its message and rerun the installer"
        return 1
    fi
    oryx_status enrol "oryx holds $public_key"
}

oryx_token_file_is_valid() {
    local file="$1"

    [[ "$(wc -l < "$file")" -eq 2 ]] \
        && grep -qxE 'TUNNEL_SERVICE_TOKEN_ID=[A-Za-z0-9._-]+' "$file" \
        && grep -qxE 'TUNNEL_SERVICE_TOKEN_SECRET=[A-Za-z0-9._-]+' "$file"
}

oryx_token() {
    local token_dir="$config_home/forward"
    local token_file="$token_dir/tunnel-oryx.env"
    local token_tmp
    local status

    ORYX_TOKEN_REPLACED=0
    if ! mkdir -p "$token_dir"; then
        ORYX_ERROR="could not create $token_dir"
        return 1
    fi
    if [[ "$(stat -c %a "$token_dir")" != 700 ]] && ! chmod 700 "$token_dir"; then
        ORYX_ERROR="could not set mode 0700 on $token_dir"
        return 1
    fi
    if ! token_tmp="$(umask 077; mktemp "$token_dir/.tunnel-oryx-XXXXXX")"; then
        ORYX_ERROR="could not make a temporary token file in $token_dir"
        return 1
    fi
    trap 'rm -f -- "${token_tmp:-}"' EXIT

    if ssh oryx 'secrets CF_ACCESS_ORYX_TUNNEL_ID CF_ACCESS_ORYX_TUNNEL_SECRET -- sh -c '\''printf "TUNNEL_SERVICE_TOKEN_ID=%s\nTUNNEL_SERVICE_TOKEN_SECRET=%s\n" "$CF_ACCESS_ORYX_TUNNEL_ID" "$CF_ACCESS_ORYX_TUNNEL_SECRET"'\''' > "$token_tmp"; then
        :
    else
        status=$?
        ORYX_ERROR="could not copy the Cloudflare Access token from oryx (ssh exited $status)"
        rm -f -- "$token_tmp"
        trap - EXIT
        return 1
    fi
    if ! oryx_token_file_is_valid "$token_tmp"; then
        ORYX_ERROR="oryx returned a malformed Cloudflare Access token file"
        rm -f -- "$token_tmp"
        trap - EXIT
        return 1
    fi

    if [[ -f "$token_file" ]] && cmp -s "$token_tmp" "$token_file"; then
        if [[ "$(stat -c %a "$token_file")" != 600 ]] && ! chmod 600 "$token_file"; then
            ORYX_ERROR="could not set mode 0600 on $token_file"
            rm -f -- "$token_tmp"
            trap - EXIT
            return 1
        fi
        rm -f -- "$token_tmp"
        trap - EXIT
        oryx_status token "unchanged"
        return 0
    fi
    if ! chmod 600 "$token_tmp"; then
        ORYX_ERROR="could not set mode 0600 on the temporary token file"
        rm -f -- "$token_tmp"
        trap - EXIT
        return 1
    fi
    if ! mv -f -- "$token_tmp" "$token_file"; then
        ORYX_ERROR="could not replace $token_file"
        rm -f -- "$token_tmp"
        trap - EXIT
        return 1
    fi
    trap - EXIT
    ORYX_TOKEN_REPLACED=1
    oryx_status token "replaced"
}

oryx_unit() {
    local unit_dir="$HOME/.config/systemd/user"
    local unit="$unit_dir/forward-oryx.service"

    if ! mkdir -p "$unit_dir"; then
        ORYX_ERROR="could not create $unit_dir"
        return 1
    fi
    if ! ensure_link "$DOTFILES_DIR/forward/forward-oryx.service" "$unit"; then
        ORYX_ERROR="could not link $unit"
        return 1
    fi
    if ! systemctl --user daemon-reload; then
        ORYX_ERROR="could not reload user units"
        return 1
    fi
    oryx_status unit "linked forward-oryx.service"
}

oryx_process_is_running() {
    local process_stat

    { process_stat="$(<"/proc/$1/stat")"; } 2>/dev/null || return 1
    [[ "${process_stat##*) }" != Z* ]]
}

oryx_old_units() {
    local found_units
    local unit
    local socket
    local pids
    local pid
    local deadline
    local token_dir="$HOME/.config/systemd/user/forward-tunnel@oryx.service.d"
    local token_file="$token_dir/token.conf"
    local changed=0
    local actions=0
    local -a units=()

    if found_units="$(find_units forward-daemon@oryx.service forward-tunnel@oryx.service)"; then
        :
    else
        case $? in
            1) ORYX_ERROR="could not list the old unit files" ;;
            2) ORYX_ERROR="could not list the old unit instances" ;;
        esac
        return 1
    fi
    while read -r unit; do
        case "$unit" in
            forward-daemon@oryx.service|forward-tunnel@oryx.service)
                units+=("$unit")
                ;;
        esac
    done <<< "$found_units"
    for unit in "${units[@]}"; do
        if ! systemctl --user disable --now "$unit"; then
            ORYX_ERROR="could not disable $unit; run the installer again"
            return 1
        fi
        actions=$((actions + 1))
        oryx_status "old units" "disabled $unit"
    done

    if [[ -L "$token_file" ]]; then
        if ! rm -f -- "$token_file"; then
            ORYX_ERROR="could not remove $token_file; run the installer again"
            return 1
        fi
        changed=1
        actions=$((actions + 1))
        oryx_status "old units" "removed $token_file"
    fi
    if [[ -d "$token_dir" && ! -L "$token_dir" ]] && rmdir -- "$token_dir" 2>/dev/null; then
        changed=1
        actions=$((actions + 1))
        oryx_status "old units" "removed empty $token_dir"
    fi
    if (( changed )) && ! systemctl --user daemon-reload; then
        ORYX_ERROR="could not reload user units after removing the old token drop-in; run the installer again"
        return 1
    fi

    for socket in \
        "$HOME/.ssh/sockets/tunnel-agent@oryx.tail12ec1c.ts.net" \
        "$HOME/.ssh/sockets/tunnel-agent@oryx-pcsc.thecybermonk.com"; do
        [[ -S "$socket" ]] || continue
        ssh -o "ControlPath=$socket" -O exit oryx-old >/dev/null 2>&1 || true
        actions=$((actions + 1))
        oryx_status "old units" "closed SSH master $socket"
    done

    pids="$(pgrep -u "$(id -u)" -x -f 'ssh -N -f oryx(-cf)?-tunnel' || true)"
    while read -r pid; do
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        kill -TERM "$pid" 2>/dev/null || true
        deadline=$((SECONDS + 10))
        while oryx_process_is_running "$pid"; do
            if (( SECONDS >= deadline )); then
                ORYX_ERROR="old tunnel process $pid did not stop; run the installer again"
                return 1
            fi
            sleep 1
        done
        actions=$((actions + 1))
        oryx_status "old units" "ended SSH tunnel process $pid"
    done <<< "$pids"

    if (( actions == 0 )); then
        oryx_status "old units" "no old oryx units or SSH tunnels were running"
    fi
}

oryx_foreign_listener() {
    local listeners
    local listener
    local local_address
    local address
    local port

    if ! listeners="$(ss -Hltnp)"; then
        ORYX_ERROR="could not inspect listeners with ss -Hltnp"
        return 2
    fi
    while read -r listener; do
        read -r _ _ _ local_address _ <<< "$listener"
        port="${local_address##*:}"
        address="${local_address%:*}"
        address="${address%%%*}"
        case "$port" in 12801|12802|12805) ;; *) continue ;; esac
        # A wildcard listener on the port blocks ssh's 127.0.0.1 bind as surely as a loopback one.
        case "$address" in
            127.0.0.1|0.0.0.0|'*'|'[::]'|'[::ffff:127.0.0.1]'|'[::ffff:0.0.0.0]')
                ORYX_LISTENER="$listener"
                return 0
                ;;
        esac
    done <<< "$listeners"
    return 1
}

oryx_is_stale() {
    local started
    local outcome
    local status

    if ! started="$(systemctl --user show forward-oryx -p ExecMainStartTimestamp --timestamp=us+utc --value)"; then
        ORYX_ERROR="could not read forward-oryx's start time"
        return 2
    fi
    if outcome="$(python3 - "$started" \
        "$config_home/forward/tunnel-oryx.env" \
        "$DOTFILES_DIR/forward/forward-oryx.service" \
        "$DOTFILES_DIR/forward/forward-oryx" \
        "$DOTFILES_DIR/forward/forward-oryx.ssh-config" \
        "$DOTFILES_DIR/forward/forward-oryx.known_hosts" \
        "$DOTFILES_DIR/forward/config-daemon-oryx.toml" \
        "$DOTFILES_DIR/forward/forward-daemon" <<'PY'
import calendar
from datetime import datetime, timezone
import os
import sys

try:
    started = datetime.strptime(
        sys.argv[1], "%a %Y-%m-%d %H:%M:%S.%f UTC"
    ).replace(tzinfo=timezone.utc)
except ValueError as error:
    print(f"could not parse forward-oryx's start time: {error}", file=sys.stderr)
    print("start")
    raise SystemExit(2)

newest = 0
for path in sys.argv[2:]:
    try:
        mtime_ns = os.stat(path).st_mtime_ns
    except OSError as error:
        print(f"could not read the mtime of {path}: {error}", file=sys.stderr)
        print(f"mtime:{path}")
        raise SystemExit(2)
    newest = max(newest, mtime_ns)

started_us = calendar.timegm(started.utctimetuple()) * 1_000_000 + started.microsecond
raise SystemExit(0 if started_us * 1_000 <= newest else 1)
PY
)"; then
        return 0
    else
        status=$?
    fi
    if (( status == 1 )); then
        return 1
    fi
    case "$outcome" in
        mtime:*) ORYX_ERROR="could not read the mtime of ${outcome#mtime:}" ;;
        *) ORYX_ERROR="forward-oryx has no usable start time" ;;
    esac
    return 2
}

oryx_start() {
    local enabled
    local active

    if ! enabled="$(systemctl --user is-enabled forward-oryx)"; then
        enabled=""
    fi
    if [[ "$enabled" != enabled ]]; then
        if ! systemctl --user enable forward-oryx; then
            ORYX_ERROR="could not enable forward-oryx"
            return 1
        fi
    fi
    if ! active="$(systemctl --user is-active forward-oryx)"; then
        active=""
    fi
    if [[ "$active" != active ]]; then
        if oryx_foreign_listener; then
            ORYX_ERROR="a listener already uses an oryx forward port: $ORYX_LISTENER"
            return 1
        elif [[ $? -eq 2 ]]; then
            return 1
        fi
        if ! systemctl --user start forward-oryx; then
            ORYX_ERROR="could not start forward-oryx"
            return 1
        fi
        oryx_status start "started forward-oryx"
        return 0
    fi
    local stale=0
    if (( ORYX_TOKEN_REPLACED )); then
        stale=1
    elif oryx_is_stale; then
        stale=1
    elif (( $? != 1 )); then
        # oryx_is_stale: 0 stale, 1 current, 2 could not tell (ORYX_ERROR says why).
        return 1
    fi
    if (( stale )); then
        if ! systemctl --user restart forward-oryx; then
            ORYX_ERROR="could not restart forward-oryx"
            return 1
        fi
        oryx_status start "restarted forward-oryx"
    else
        oryx_status start "forward-oryx is already current"
    fi
}

oryx_listener_belongs_to_unit() {
    local main_pid
    local listeners
    local listener
    local pid
    local parent

    if ! main_pid="$(systemctl --user show forward-oryx -p MainPID --value)" \
        || ! [[ "$main_pid" =~ ^[1-9][0-9]*$ ]]; then
        return 1
    fi
    if ! listeners="$(ss -Hltnp)"; then
        return 1
    fi
    while read -r listener; do
        [[ "$listener" == *127.0.0.1:12802* ]] || continue
        [[ "$listener" =~ pid=([0-9]+) ]] || continue
        pid="${BASH_REMATCH[1]}"
        if ! parent="$(ps -o ppid= -p "$pid")"; then
            continue
        fi
        parent="${parent//[[:space:]]/}"
        if [[ "$parent" = "$main_pid" ]]; then
            ORYX_MAIN_PID="$main_pid"
            return 0
        fi
    done <<< "$listeners"
    return 1
}

oryx_connected() {
    local wait_seconds="${FORWARD_ORYX_WAIT_SECONDS:-60}"
    local settle_seconds="${FORWARD_ORYX_SETTLE_SECONDS:-3}"
    local deadline
    local active
    local main_pid

    if ! [[ "$wait_seconds" =~ ^[0-9]+$ && "$settle_seconds" =~ ^[0-9]+$ ]]; then
        ORYX_ERROR="FORWARD_ORYX_WAIT_SECONDS and FORWARD_ORYX_SETTLE_SECONDS must be whole seconds"
        return 1
    fi
    deadline=$((SECONDS + wait_seconds))
    while ! oryx_listener_belongs_to_unit; do
        if (( SECONDS >= deadline )); then
            ORYX_ERROR="forward-oryx did not expose 127.0.0.1:12802 from its MainPID; inspect journalctl --user -u forward-oryx -n 30"
            return 1
        fi
        sleep 1
    done
    main_pid="$ORYX_MAIN_PID"
    if (( settle_seconds )); then
        sleep "$settle_seconds"
    fi
    if ! active="$(systemctl --user show forward-oryx -p ActiveState --value)"; then
        ORYX_ERROR="could not read forward-oryx's state; inspect journalctl --user -u forward-oryx -n 30"
        return 1
    fi
    if ! [[ "$(systemctl --user show forward-oryx -p MainPID --value)" = "$main_pid" && "$active" = active ]]; then
        ORYX_ERROR="forward-oryx changed state while settling; inspect journalctl --user -u forward-oryx -n 30"
        return 1
    fi
    oryx_status connected "forward-oryx is running"
}

oryx_cloudflared() {
    if ! bash "$DOTFILES_DIR/installers/cloudflared.sh"; then
        ORYX_ERROR="installers/cloudflared.sh failed; see its message above"
        return 1
    fi
    oryx_status cloudflared "cloudflared is installed"
}

install_oryx() {
    ORYX_ERROR=""
    ORYX_TOKEN_REPLACED=0
    oryx_cloudflared || oryx_fail cloudflared "$ORYX_ERROR"
    oryx_key || oryx_fail key "$ORYX_ERROR"
    oryx_enrol || oryx_fail enrol "$ORYX_ERROR"
    oryx_token || oryx_fail token "$ORYX_ERROR"
    oryx_unit || oryx_fail unit "$ORYX_ERROR"
    oryx_old_units || oryx_fail "old units" "$ORYX_ERROR"
    oryx_start || oryx_fail start "$ORYX_ERROR"
    oryx_connected || oryx_fail connected "$ORYX_ERROR"
}

[[ "${XDG_CONFIG_HOME:-$config_home}" = "$config_home" ]] \
    || fail "$role" "XDG_CONFIG_HOME must be unset or exactly $config_home; Forward's user units and agent boxes use ~/.config/forward."

host="$(hostname -s)"
if [[ "$role" = serve ]] && is_tunnel_pair "$host"; then
    fail "$role" "oryx's forward serve config is written by oryx-edge-cutover (sjawhar/dojo platform/oryx-edge)"
fi

command -v python3 >/dev/null || fail "$role" "install Python 3.11 or newer, then rerun this installer."
command -v tailscale >/dev/null || fail "$role" "install Tailscale, join the tailnet, then rerun this installer."

# Resolve and validate the entire inventory before replacing a config or touching
# units. Atomic replacement detaches an old config symlink without writing through
# it into the committed per-pair file, which remains available until migration step 4.
python3 - "$DOTFILES_DIR" "$role" "$host" "$config_home/forward/config.toml" <<'PY'
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

try:
    import tomllib
except ImportError:
    sys.exit("forward.sh: install Python 3.11 or newer, then rerun this installer.")

root, role, host, output = sys.argv[1:]
inventory = Path(root) / "forward/hosts.toml"


def fail(message):
    raise ValueError(message)


def resolve(name=None):
    command = ["tailscale", "ip", "-4"] + ([name] if name else [])
    remedy = f"check `{' '.join(command)}` and `tailscale status`, then rerun the installer"
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
        address = ipaddress.IPv4Address(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        fail(f"cannot resolve {name or 'this machine'} to one tailnet IPv4 address: {error}; {remedy}.")
    if address not in ipaddress.IPv4Network("100.64.0.0/10"):
        fail(f"{' '.join(command)} returned {address}, not a Tailscale IPv4 address; {remedy}.")
    return str(address)


try:
    hosts = tomllib.loads(inventory.read_text(encoding="utf-8"))
    laptop, vms = hosts.get("laptop"), hosts.get("dev_vms")
    if not isinstance(vms, list) or not vms:
        fail(f"{inventory}: dev_vms must be a nonempty list of Tailscale VM names.")
    names = [laptop, *vms]
    if any(not isinstance(name, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", name) for name in names):
        fail(f"{inventory}: laptop and dev_vms must contain short Tailscale names (letters, digits, hyphens), not addresses or commands.")
    if len(set(names)) != len(names):
        fail(f"{inventory}: laptop and dev_vms must name distinct machines; remove duplicate names.")
    if role == "serve" and host not in vms:
        fail(f"{host} is not a known dev VM in {inventory}; add its Tailscale name there before installing serve.")

    own = resolve()
    lines = ["# Generated by installers/forward.sh; reinstall to refresh tailnet addresses.", f"listen = {json.dumps(own)}"]
    if role == "daemon":
        peers = []
        owners = {own: "this machine"}
        for name in vms:
            address = resolve(name)
            if address in owners:
                fail(f"{inventory}: {name} and {owners[address]} resolve to the same address {address}; check `tailscale ip -4 {name}` and fix the inventory.")
            owners[address] = name
            peers.append(address)
        lines.append(f"peers = {json.dumps(peers)}")
        lines.append((Path(root) / "forward/daemon-policy.toml").read_text(encoding="utf-8"))
    else:
        peer = resolve(laptop)
        if peer == own:
            fail(f"this VM and laptop {laptop} resolve to {own}; check `tailscale ip -4 {laptop}` and {inventory}.")
        lines += [f"peer = {json.dumps(peer)}", "forward_ttl_secs = 300"]
    contents = "\n".join(lines) + "\n"
    tomllib.loads(contents)
    path = Path(output)
    if path.is_symlink() or not path.is_file() or path.read_text(encoding="utf-8") != contents:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".config-", suffix=".toml", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(contents)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
except (OSError, ValueError) as error:
    sys.exit(f"forward.sh {role}: {error}")
PY

if ! "$MISE" which forward >/dev/null 2>&1; then
    "$MISE" install forward
fi
if [[ "$role" = daemon ]] && ! "$MISE" which omp >/dev/null 2>&1; then
    "$MISE" install "github:sjawhar/oh-my-pi@latest"
fi

mkdir -p "${HOME}/.config/systemd/user"
for unit in "${units[@]}"; do
    ensure_link "${DOTFILES_DIR}/forward/${unit}" "${HOME}/.config/systemd/user/${unit}"
done
# These files are the obsolete ambient browser bypass, not migration artifacts.
rm -f "${config_home}/omp/browser-relay.yml" "${config_home}/environment.d/browser-relay.conf"
systemctl --user daemon-reload || fail "$role" "could not reload user units; run from the target machine's user session."

if [[ "$role" = daemon ]]; then
    # Include enabled-but-unloaded and active-but-disabled instances, including
    # retired names no longer in hosts.toml. Preserve every unit/config/SSH file.
    if legacy_unit_names="$(find_units 'forward-daemon@*.service' 'forward-tunnel@*.service')"; then
        :
    else
        case $? in
            1) fail "$role" "cannot list legacy unit files; check systemctl --user before retrying migration step 2." ;;
            2) fail "$role" "cannot list legacy unit instances; check systemctl --user before retrying migration step 2." ;;
        esac
    fi
    legacy_units=()
    while read -r unit; do
        case "$unit" in
            forward-daemon@?*.service|forward-tunnel@?*.service)
                legacy_units+=("$unit")
                ;;
        esac
    done <<< "$legacy_unit_names"
    for unit in "${legacy_units[@]}"; do
        pair="${unit#*@}"
        pair="${pair%.service}"
        if is_tunnel_pair "$pair"; then
            # The oryx part below disables and removes these two old instances.
            continue
        fi
        systemctl --user disable --now "$unit" \
            || fail "$role" "could not disable $unit; check systemctl --user status $unit before retrying migration step 2."
    done
fi

if [[ "$role" = serve ]]; then
    # The legacy SSH bridge owns the same PC/SC socket, even when disabled but
    # still active. Stop it before starting serve, retaining its unit for step 4.
    bridge_files="$(systemctl --user list-unit-files --no-legend --no-pager --type=service)" \
        || fail "$role" "cannot list pcscd-bridge.service; check systemctl --user before retrying migration step 3."
    bridge_active="$(systemctl --user list-units --all --full --plain --no-legend --no-pager pcscd-bridge.service)" \
        || fail "$role" "cannot list loaded pcscd-bridge.service; check systemctl --user before retrying migration step 3."
    while read -r unit state rest; do
        if [[ "$unit" = pcscd-bridge.service ]]; then
            if [[ "$state" = not-found ]]; then
                systemctl --user stop "$unit" \
                    || fail "$role" "could not stop missing pcscd-bridge.service; inspect its loaded state before retrying."
                systemctl --user reset-failed "$unit" \
                    || fail "$role" "could not reset failed pcscd-bridge.service; inspect its loaded state before retrying."
            else
                systemctl --user disable --now "$unit" \
                    || fail "$role" "could not stop and disable pcscd-bridge.service; inspect journalctl --user -u pcscd-bridge.service before retrying migration step 3."
            fi
            break
        fi
    done <<<"${bridge_files}
${bridge_active}"
    mkdir -p "$HOME/.pcscd"
fi

for unit in "${units[@]}"; do
    service="${unit%.service}"
    systemctl --user enable --now "$service" \
        || fail "$role" "could not enable $service; inspect journalctl --user -u $service."
    # A prior attempt may have replaced config before a later install step failed.
    # Restart on every deliberate install, even when the file is already current.
    systemctl --user restart "$service" \
        || fail "$role" "could not restart $service with the generated config; inspect journalctl --user -u $service."
done

if [ "${1:-}" = daemon ]; then
    "$MISE" exec "github:sjawhar/oh-my-pi" -- omp browser-relay install --dir "$HOME/.omp/browser-relay/extension"
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
    install_oryx
fi
