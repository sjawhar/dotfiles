#!/bin/bash
# Box startup: start inner Docker and host-service relays, preserve the host's
# credential routes, then run the session as its user. Box lifetime follows the session.
set -euo pipefail

# Required launcher address for the Envoy relay.
[[ -n "${AGENTBOX_ENVOY_FORWARDER:-}" ]] || { echo "agentbox: AGENTBOX_ENVOY_FORWARDER is not set" >&2; exit 1; }
[[ -n "${AGENTBOX_USER:-}" ]] || { echo "agentbox: AGENTBOX_USER is not set" >&2; exit 1; }

# Set the password-database home before any relay starts as the session user.
# usermod refuses to change a user whose processes already run.
if [ "$(getent passwd "$AGENTBOX_USER" | cut -d: -f6)" != "$HOME" ]; then
    usermod -d "$HOME" "$AGENTBOX_USER"
fi

# Bind mounts can share the home device. JSON/NUL preserves literal target names.
ownership_paths="$(mktemp /tmp/agentbox-ownership.XXXXXX)"
if ! findmnt --json --list --output TARGET | /usr/bin/python3 -c '
import json, os, sys
try:
    targets = [row["target"] for row in json.load(sys.stdin)["filesystems"]]
    if not targets or any(not isinstance(target, str) or "\0" in target for target in targets):
        raise ValueError()
    for target in targets:
        sys.stdout.buffer.write(os.fsencode(target) + b"\0")
except (KeyError, TypeError, ValueError, UnicodeError):
    sys.exit("agentbox: invalid mount target JSON")
' >"$ownership_paths"; then
    rm -f "$ownership_paths"
    echo "agentbox: cannot enumerate host mounts; no ownership changes made" >&2
    exit 1
fi
home_find=("$HOME" -xdev)
while IFS= read -r -d '' mounted; do
    [[ "$mounted" == "$HOME/"* ]] || continue
    home_find+=(-samefile "$mounted" -prune -o)
done <"$ownership_paths"
if ! find "${home_find[@]}" -type d -print0 >"$ownership_paths"; then
    rm -f "$ownership_paths"
    echo "agentbox: cannot enumerate private home directories; no ownership changes made" >&2
    exit 1
fi
while IFS= read -r -d '' dir; do
    chown "$AGENTBOX_USER:$AGENTBOX_USER" "$dir"
done <"$ownership_paths"
rm -f "$ownership_paths"

# Every --dir the launcher named must really be the HOST's directory in here.
#
# Why this is checked at all: inside a box an unbound path is NOT an error. The directory
# exists, it is writable, it is empty, and it is the box's own disk, so a session works in it
# for hours and loses everything in it when the box ends. Five sessions reported exactly that
# on 2026-11-05 (dispatch://INFRA-1192/artifact/frictions-md, friction f1), each having
# "checked" the path by listing it -- a negative that could not have returned a positive.
#
# The test: `findmnt -T PATH` names the mount the path lives on. A bound --dir answers with
# itself, or with a bound parent (a --dir under /mnt/durable/<user> answers that, since the
# launcher does not bind it a second time); an unbound path answers with the container's own
# root. Measured in agentbox-998b80ebbc2a on 2026-11-05: all 16 of its --dir paths answered xfs
# at their own path or a parent, while a path that was never bound answered "/ overlay".
#
# The first --dir is the directory the session works in, so an unbound one is always fatal: a
# box in any other directory is not that session's box. For the rest the rule follows the
# launcher's own: fatal on a fresh launch, and on a resume (AGENTBOX_DROP_GONE_DIRS, set by
# `agentbox new --drop-gone-dirs`) said loudly and left out, because a refusal there leaves a
# live session's pane at a shell until a person runs omp by hand.
if [[ -n "${AGENTBOX_DIRS:-}" ]]; then
    unbound_fatal=() unbound_warn=() dir_index=0
    while IFS= read -r box_dir; do
        [[ -n "$box_dir" ]] || continue
        dir_index=$((dir_index + 1))
        on="$(findmnt -T "$box_dir" -n -o TARGET 2>/dev/null)" || on=""
        # An empty answer means the path is not there at all (measured: findmnt prints nothing
        # for a path that does not exist, and "/" for one on the container's own root). That is
        # not the silent case this guards: a path that is absent fails loudly at the session's
        # first use, where an unbound one reads as an empty directory for hours. So it is left
        # alone, and this check never refuses on a reading it could not take.
        [[ -n "$on" && "$on" == "/" ]] || continue
        if (( dir_index == 1 )) || [[ -z "${AGENTBOX_DROP_GONE_DIRS:-}" ]]; then
            unbound_fatal+=("$box_dir")
        else
            unbound_warn+=("$box_dir")
        fi
    done < <(printf '%s\n' "${AGENTBOX_DIRS//:/$'\n'}")
    for box_dir in ${unbound_warn[@]+"${unbound_warn[@]}"}; do
        echo "agentbox: --dir '$box_dir' is NOT bound into this box: it is this box's own empty" \
             "disk at that path, and anything written there dies with the box. This resume goes" \
             "on without it." >&2
    done
    if (( ${#unbound_fatal[@]} )); then
        {
            echo "agentbox: these --dir paths are NOT bound into this box. Each one exists here," \
                 "is writable and is EMPTY, because it is the box's own disk and not the host's" \
                 "directory, so a session working in one loses everything it writes:"
            printf '  %s\n' "${unbound_fatal[@]}"
            echo "Nothing was started. Check each on the host with: findmnt -T <path>"
        } >&2
        exit 1
    fi
fi

mkdir -p /var/log
dockerd --host=unix:///var/run/docker.sock >/var/log/dockerd.log 2>&1 &
for _ in $(seq 1 60); do
    docker --host=unix:///var/run/docker.sock info >/dev/null 2>&1 && break
    sleep 0.5
done
if ! docker --host=unix:///var/run/docker.sock info >/dev/null 2>&1; then
    echo "agentbox: inner dockerd did not start within 30s" >&2
    tail -30 /var/log/dockerd.log >&2
    exit 1
fi
chmod 666 /var/run/docker.sock

# Envoy at the address the host's envoy.json names (http://127.0.0.1:<port>),
# relayed to the host's forwarder at the network gateway, so the one file
# serves the host and every box: a box session without a profile of its own
# reads it from the mounted ~/.config/legion, and a named profile's envoy.json
# that links to it resolves there too. Run as the session user, in a session
# of its own so no signal sent to the terminal's process group reaches it, and
# restarted a second after socat exits. A relay that does not come up is
# reported, not fatal: the session starts without Envoy.
envoy_port="${AGENTBOX_ENVOY_FORWARDER##*:}"
# shellcheck disable=SC2016  # the inner bash expands $1 and $2; this shell must not
setsid setpriv --reuid="$AGENTBOX_USER" --regid="$AGENTBOX_USER" --init-groups --reset-env \
    bash -c 'while :; do socat "TCP4-LISTEN:$1,bind=127.0.0.1,fork,reuseaddr" "TCP4:$2"; sleep 1; done' \
    envoy-relay "$envoy_port" "$AGENTBOX_ENVOY_FORWARDER" </dev/null >>/var/log/envoy-relay.log 2>&1 &
envoy_relay_up() { [[ "$(ss -Hltn "sport = :$envoy_port")" == *"127.0.0.1:$envoy_port"* ]]; }
for _ in $(seq 1 20); do
    envoy_relay_up && break
    sleep 0.25
done
envoy_relay_up || echo "agentbox: no Envoy relay on 127.0.0.1:$envoy_port (/var/log/envoy-relay.log); this box's sessions cannot reach Envoy" >&2

# forward's channels, the same way. forward serves each on one of this host's loopback
# addresses (forward/AGENTS.md: a per-machine listen/peer pair, 127.0.0.2 and 127.0.0.1 on
# sami-rocqperf), and a box has a loopback of its own, so inside one every TCP channel
# refused the connection while forward's two mounted unix sockets worked: `forward open`
# could not reach the laptop and `forward browser grant` could not ask for one, which the
# skill calls a capability a box has. Each channel the launcher found
# (AGENTBOX_FORWARD_CHANNELS, "addr:port,addr:port") is put back on the same address and
# port here, pointing at the host forwarder for that port on the box network's gateway, so
# forward's own address list resolves inside exactly as on the host.
forward_gateway="${AGENTBOX_ENVOY_FORWARDER%:*}"
forward_channels="${AGENTBOX_FORWARD_CHANNELS:-}"
for channel in ${forward_channels//,/ }; do
    channel_port="${channel##*:}"
    # shellcheck disable=SC2016  # the inner bash expands $1..$3; this shell must not
    setsid setpriv --reuid="$AGENTBOX_USER" --regid="$AGENTBOX_USER" --init-groups --reset-env \
        bash -c 'while :; do socat "TCP4-LISTEN:$2,bind=$1,fork,reuseaddr" "TCP4:$3:$2"; sleep 1; done' \
        forward-relay "${channel%:*}" "$channel_port" "$forward_gateway" \
        </dev/null >>/var/log/forward-relay.log 2>&1 &
done

# Private agent-c images: keep any host route copied into the box. Only a
# registry with no auth, helper, or credential store gets the broker pull login.
# Commands run from / so mise reads the host tool store, not a workspace pin.
# shellcheck disable=SC2016  # the inner sh expands $GHCR_PULL_TOKEN; this shell must not
if ! setpriv --reuid="$AGENTBOX_USER" --regid="$AGENTBOX_USER" --init-groups --reset-env \
        env -C / HOME="$HOME" PATH="$PATH" MISE_DATA_DIR="${MISE_DATA_DIR:-}" \
        sh -c 'c="$HOME/.docker/config.json"; jq -e '\''.auths["ghcr.io"] != null or .credHelpers["ghcr.io"] != null or ((.credsStore // "") != "")'\'' "$c" >/dev/null 2>&1 && exit 0; secrets GHCR_PULL_TOKEN -- sh -c "printf %s \"\$GHCR_PULL_TOKEN\" | docker login ghcr.io -u trajectory-labs-pbc --password-stdin" >/dev/null 2>&1'; then
    echo "agentbox: ghcr.io login failed; private image pulls will fail until it is fixed" >&2
fi

# Docker Hub and dhi.io get the existing broker helper only where the host
# snapshot has no auth, helper, or global credential store for that registry.
# shellcheck disable=SC2016  # the inner sh expands $HOME and $HELPERS; this shell must not
if ! setpriv --reuid="$AGENTBOX_USER" --regid="$AGENTBOX_USER" --init-groups --reset-env \
        env -C / HOME="$HOME" PATH="$PATH" MISE_DATA_DIR="${MISE_DATA_DIR:-}" \
        HELPERS='{"dhi.io": "secretsd", "https://index.docker.io/v1/": "secretsd"}' \
        sh -c 'c="$HOME/.docker/config.json"; mkdir -p "$HOME/.docker" && { [ -s "$c" ] || echo "{}" > "$c"; } && jq --argjson h "$HELPERS" ". as \$c | .credHelpers = ((if (.credsStore // \"\") != \"\" then {} else \$h | with_entries(select(.key as \$k | ((\$c.auths // {}) | has(\$k) | not))) end) + (.credHelpers // {}))" "$c" > "$c.tmp" && mv "$c.tmp" "$c"'; then
    echo "agentbox: docker credHelpers not written; Docker Hub pulls stay anonymous" >&2
fi

# Retain existing host registry routes. Add available machine helpers only
# where the Docker snapshot has neither an auth entry nor a credential store.
# shellcheck disable=SC2016  # the inner sh expands $HOME, $v, $ok and $CRED_HELPERS; this shell must not
cred_helpers_merge='
c="$HOME/.docker/config.json"
ok=""
for v in $(printf %s "$CRED_HELPERS" | jq -r "[.[]] | unique | .[]"); do
    if command -v "docker-credential-$v" >/dev/null 2>&1; then
        ok="$ok $v"
    else
        echo "agentbox: docker-credential-$v is not on PATH; the registries the host routes to it stay unauthenticated" >&2
    fi
done
mkdir -p "$HOME/.docker" && { [ -s "$c" ] || echo "{}" > "$c"; } &&
    jq --argjson h "$CRED_HELPERS" --arg ok "$ok" ". as \$c | .credHelpers = ((if (.credsStore // \"\") != \"\" then {} else \$h | with_entries(select(.value as \$v | \$ok | split(\" \") | any(. == \$v))) | with_entries(select(.key as \$k | ((\$c.auths // {}) | has(\$k) | not))) end) + (.credHelpers // {}))" "$c" > "$c.tmp" &&
    mv "$c.tmp" "$c"
'
if [ -n "${AGENTBOX_CRED_HELPERS:-}" ] && [ "$AGENTBOX_CRED_HELPERS" != "{}" ]; then
    if ! setpriv --reuid="$AGENTBOX_USER" --regid="$AGENTBOX_USER" --init-groups --reset-env \
            env -C / HOME="$HOME" PATH="$PATH" MISE_DATA_DIR="${MISE_DATA_DIR:-}" \
            CRED_HELPERS="$AGENTBOX_CRED_HELPERS" \
            sh -c "$cred_helpers_merge"; then
        echo "agentbox: registry credHelpers not written; ECR and Artifact Registry pulls will fail unauthenticated" >&2
    fi
fi


# A mounted host SSH directory is shared state. Generate defaults only in the box.
ssh_shared=0
mountpoint -q "$HOME/.ssh" && ssh_shared=1
AGENTBOX_SSH_SHARED="$ssh_shared" /usr/local/bin/box-ssh-config "$HOME" "$AGENTBOX_USER" || \
    echo "agentbox: no ssh config written; \`ssh sami-rocqperf\` from this box may be refused" >&2

# The session runs as the mounted directories' owner with the launcher's
# environment intact (setpriv keeps env unless told otherwise).
exec setpriv --reuid="$AGENTBOX_USER" --regid="$AGENTBOX_USER" --init-groups "$@"
