#!/bin/bash
# Docker Engine from Docker's own repo (docker-ce), never Ubuntu's docker.io: docker.io pulls
# `containerd`, which conflicts with the `containerd.io` of an already-installed docker-ce, so the
# apt install fails outright on a machine that has Docker already. It also ships neither the buildx
# nor the compose plugin.
#
# By default Docker comes from the official installer (get.docker.com), at its latest version, and
# only when the machine has no `docker`. A role script turns on three more steps (devbox/gce-host.sh
# does, for sami-devbox, dispatch://INFRA-4893):
#
#   DOCKER_LIVE_RESTORE=1        dockerd's "live-restore": true in /etc/docker/daemon.json, merged
#                                with jq so every other key and value stays. With it, running
#                                containers keep running while dockerd restarts (an upgrade, a
#                                crash). The new file replaces the old one only after
#                                `dockerd --validate` accepts it; the old one stays beside it as
#                                daemon.json.bak-<UTC time>. A running dockerd then reloads it
#                                (`systemctl reload`, a SIGHUP; never a restart), and the step
#                                fails unless dockerd then reports live-restore on. A file that has
#                                it on already is not written, and a dockerd that reports it on
#                                is not reloaded.
#   DOCKER_CE_VERSION=<version>  docker-ce and docker-ce-cli at exactly this apt version (for
#                                example 5:29.9.3-1~ubuntu.24.04~noble), from Docker's apt
#                                repository, and pinned there in
#                                /etc/apt/preferences.d/docker-ce.pref. Installing another version
#                                restarts dockerd. An installed version newer than the pin fails
#                                the install: apt refuses a downgrade without --allow-downgrades,
#                                which this never passes. Ubuntu only.
#   DOCKER_CONNTRACK_TIME_WAIT=1 devbox/sysctl-conntrack.conf as
#                                /etc/sysctl.d/90-conntrack-time-wait.conf: conntrack forgets a
#                                closed TCP flow after 15 s, not the kernel's 120 s, which shortens
#                                the conntrack dump dockerd makes under one host-wide lock at each
#                                container start and exit (the file says why). installers/lib.sh's
#                                ensure_sysctl_conf writes the file only when it differs from the
#                                repository's, and `sysctl -p` applies it only when it was written or
#                                the kernel holds another value; the step then fails unless the
#                                kernel holds the file's value.
#
# live-restore goes first, so a version change restarts a dockerd that has it on already; the
# conntrack step goes last. Paths these steps write or read are under INSTALL_ROOT, which is empty
# unless a test sets it. install.sh sources this file, so their variables are local to functions.
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Write $3 and a newline, as root, with mode $2, to a new file in $1's directory, and print its
# path. The caller moves it over $1 (one rename, so no reader sees half a file) or removes it.
docker_stage() {
    local path="$1" mode="$2" content="$3" dir staged
    dir="${path%/*}"
    if [[ ! -d "$dir" ]]; then
        sudo install -d -m 0755 "$dir" || return 1
    fi
    staged="$(sudo mktemp "$dir/.${path##*/}.XXXXXX")" || return 1
    if ! printf '%s\n' "$content" | sudo tee "$staged" >/dev/null || ! sudo chmod "$mode" "$staged"; then
        sudo rm -f -- "$staged"
        return 1
    fi
    printf '%s\n' "$staged"
}

# Replace $1 with $3 and a newline, mode $2, in one rename.
docker_write() {
    local staged
    staged="$(docker_stage "$@")" || return 1
    if ! sudo mv -f -- "$staged" "$1"; then
        sudo rm -f -- "$staged"
        return 1
    fi
}

# True when the running dockerd reports live-restore on. A dockerd that gives no answer in 10 s
# does not.
docker_live_restore_running() {
    [[ "$(sudo timeout 10 docker info -f '{{.LiveRestoreEnabled}}' 2>/dev/null || true)" == true ]]
}

# DOCKER_LIVE_RESTORE (header). ensure_json (lib.sh) does not fit: this file is root's, and it
# must pass `dockerd --validate` before it replaces the old one.
docker_live_restore() {
    local conf="${INSTALL_ROOT:-}/etc/docker/daemon.json" merged staged backup="" changed=0 deadline
    command -v jq >/dev/null || { echo "docker.sh: DOCKER_LIVE_RESTORE needs jq" >&2; return 1; }
    # A link stays a link: its target gets the change, as with ensure_json.
    if [[ -L "$conf" ]]; then
        conf="$(readlink -f -- "$conf")" || return 1
    fi
    if [[ -e "$conf" ]] && jq -e '.["live-restore"] == true' "$conf" >/dev/null; then
        echo "docker.sh: live-restore is on in $conf already"
    else
        if [[ -e "$conf" ]]; then
            # -e: an empty file gives no output, and that fails too. --indent 4, as sysbox-ce's
            # install script writes the file.
            merged="$(jq -e --indent 4 '.["live-restore"] = true' "$conf")" || {
                echo "docker.sh: $conf is not a JSON object; changed nothing" >&2
                return 1
            }
        else
            merged="$(jq -n --indent 4 '{"live-restore": true}')" || return 1
        fi
        staged="$(docker_stage "$conf" 0644 "$merged")" || return 1
        if command -v dockerd >/dev/null && ! sudo dockerd --validate --config-file "$staged"; then
            sudo rm -f -- "$staged"
            echo "docker.sh: dockerd --validate refused live-restore in $conf; changed nothing" >&2
            return 1
        fi
        if [[ -e "$conf" ]]; then
            # A hard link to the old file: the backup is that file whole, never a partial copy.
            backup="$conf.bak-$(date -u +%Y%m%dT%H%M%SZ)"
            if ! sudo ln -- "$conf" "$backup"; then
                sudo rm -f -- "$staged"
                return 1
            fi
        fi
        if ! sudo mv -f -- "$staged" "$conf"; then
            sudo rm -f -- "$staged" ${backup:+"$backup"}
            return 1
        fi
        changed=1
        echo "docker.sh: turned live-restore on in $conf${backup:+ (the old file is $backup)}"
    fi

    systemctl is-active --quiet docker.service || return 0
    # The running dockerd's answer decides, not the file's: a reload that failed, or a run that
    # stopped before it, leaves the file on and dockerd off, and a version change after this
    # would then stop every container.
    if ((!changed)) && docker_live_restore_running; then
        return 0
    fi
    sudo systemctl reload docker.service || {
        echo "docker.sh: systemctl reload docker.service failed; $conf has live-restore on" >&2
        return 1
    }
    # The reload only sends the SIGHUP; dockerd applies the file after it.
    deadline=$((SECONDS + 10))
    until docker_live_restore_running; do
        if ((SECONDS >= deadline)); then
            echo "docker.sh: dockerd does not report live-restore on 10 s after systemctl reload docker.service (journalctl -u docker.service)" >&2
            return 1
        fi
        sleep 1
    done
    echo "docker.sh: dockerd reloaded $conf and reports live-restore on"
}

# True when docker-ce and docker-ce-cli are both fully installed (dpkg status `installed`; a held
# package counts) at version $1.
docker_ce_at() {
    local package
    for package in docker-ce docker-ce-cli; do
        [[ "$(dpkg-query -W -f='${db:Status-Status} ${Version}' "$package" 2>/dev/null || true)" == "installed $1" ]] || return 1
    done
}

# DOCKER_CE_VERSION (header). The repository is the one get.docker.com and Docker's Ubuntu install
# guide set up, at the same paths, so a machine that has it already gets no second copy.
docker_ce_pin() {
    local version="$1" root="${INSTALL_ROOT:-}" codename arch key pin packages
    local keyring=/etc/apt/keyrings/docker.asc
    local sources="$root/etc/apt/sources.list.d/docker.list"
    local preferences="$root/etc/apt/preferences.d/docker-ce.pref"
    # In a subshell, so os-release's variables do not reach install.sh.
    # shellcheck source=/dev/null
    codename="$(. "$root/etc/os-release" && [[ "$ID" == ubuntu ]] && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")" || {
        echo "docker.sh: DOCKER_CE_VERSION installs from Docker's Ubuntu repository, and $root/etc/os-release is not Ubuntu's" >&2
        return 1
    }
    if [[ ! -s "$root$keyring" ]]; then
        key="$(curl -fsSL --retry 3 https://download.docker.com/linux/ubuntu/gpg)" || return 1
        docker_write "$root$keyring" 0644 "$key" || return 1
    fi
    if [[ ! -e "$sources" ]]; then
        arch="$(dpkg --print-architecture)" || return 1
        docker_write "$sources" 0644 \
            "deb [arch=$arch signed-by=$keyring] https://download.docker.com/linux/ubuntu $codename stable" || return 1
    fi
    pin="# Pinned by installers/docker.sh (DOCKER_CE_VERSION, set by devbox/gce-host.sh); to upgrade, change docker_ce_version in devbox/gce-host.sh and run it again.
Package: docker-ce docker-ce-cli
Pin: version $version
Pin-Priority: 1001"
    if [[ ! -f "$preferences" || "$(<"$preferences")" != "$pin" ]]; then
        docker_write "$preferences" 0644 "$pin" || return 1
    fi

    if docker_ce_at "$version"; then
        echo "docker.sh: docker-ce and docker-ce-cli are at $version already"
        return 0
    fi
    packages=("docker-ce=$version" "docker-ce-cli=$version")
    # A machine without Docker also gets the rest of what get.docker.com installs, at the
    # repository's latest version.
    if [[ "$(dpkg-query -W -f='${db:Status-Status}' docker-ce 2>/dev/null || true)" != installed ]]; then
        packages+=(containerd.io docker-buildx-plugin docker-compose-plugin)
    fi
    sudo apt-get update -qq || return 1
    # No --allow-downgrades: over a newer installed version, apt refuses and the install fails.
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}" || return 1
    if ! docker_ce_at "$version"; then
        echo "docker.sh: after apt-get install, docker-ce or docker-ce-cli is not at $version" >&2
        return 1
    fi
    echo "docker.sh: installed docker-ce and docker-ce-cli $version"
}

if command -v apt-get &>/dev/null; then
    if [[ "${DOCKER_LIVE_RESTORE:-}" == 1 ]]; then
        docker_live_restore
    fi
    if [[ -n "${DOCKER_CE_VERSION:-}" ]]; then
        docker_ce_pin "$DOCKER_CE_VERSION"
    else
        ensure_command docker "curl -fsSL https://get.docker.com | sudo sh"
    fi
    if [[ "${DOCKER_CONNTRACK_TIME_WAIT:-}" == 1 ]]; then
        ensure_sysctl_conf "$DOTFILES_DIR/devbox/sysctl-conntrack.conf" \
            /etc/sysctl.d/90-conntrack-time-wait.conf net.netfilter.nf_conntrack_tcp_timeout_time_wait
    fi

    # Tooling that drives Docker over SSH — Pulumi's docker provider, the envoy
    # listener deploy — runs `docker` as the login user with no tty for sudo, so
    # socket access has to come from group membership. Skipped when the group is
    # absent, which is the socket-mounted devcontainer case: the CLI is real but
    # the engine is the host's.
    if getent group docker >/dev/null && ! id -nG "$USER" | grep -qw docker; then
        sudo usermod -aG docker "$USER"
        echo "NOTE: Added $USER to docker group. Start a new login session (or use 'sg docker') for it to apply."
    fi
fi
