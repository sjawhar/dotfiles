#!/bin/bash
# Box startup, as root: bring up the inner dockerd, keep the containers it
# will run away from the host's metadata forwarder, relay the box's own Envoy
# address to the host's Envoy forwarder, log the inner docker into ghcr.io as
# ubuntu, then hand off to the session command as ubuntu. The session command
# is the container's main process (scripts/agentbox passes shims/omp), so the
# box lives exactly as long as the session and `--rm` cleans it up; the daemon
# and the relay die with it.
set -euo pipefail

# The forwarder addresses are required because what follows depends on them:
# the launcher always passes both, so a missing one is a launcher bug, not a
# box that silently starts sandboxes with the instance role's access, or one
# whose sessions cannot reach Envoy.
[[ -n "${AGENTBOX_IMDS_FORWARDER:-}" ]] || { echo "agentbox: AGENTBOX_IMDS_FORWARDER is not set" >&2; exit 1; }
[[ -n "${AGENTBOX_ENVOY_FORWARDER:-}" ]] || { echo "agentbox: AGENTBOX_ENVOY_FORWARDER is not set" >&2; exit 1; }
[[ -n "${AGENTBOX_USER:-}" ]] || { echo "agentbox: AGENTBOX_USER is not set" >&2; exit 1; }

# Set the password-database home before any relay starts as the session user.
# usermod refuses to change a user whose processes already run.
if [ "$(getent passwd "$AGENTBOX_USER" | cut -d: -f6)" != "$HOME" ]; then
    usermod -d "$HOME" "$AGENTBOX_USER"
fi

# Give the session user its own home directories, not host mounts. Bind mounts
# can have the same device number as the box home, so prune their exact paths.
mount_targets="$(findmnt -rn -o TARGET --raw)"
home_find=("$HOME" -xdev)
while IFS= read -r mounted; do
    printf -v mounted '%b' "$mounted"
    [[ "$mounted" == "$HOME/"* ]] || continue
    home_find+=(-path "$mounted" -prune -o)
done <<<"$mount_targets"
while IFS= read -r -d '' dir; do
    chown "$AGENTBOX_USER:$AGENTBOX_USER" "$dir"
done < <(find "${home_find[@]}" -type d -print0)

mkdir -p /var/log
dockerd --host=unix:///var/run/docker.sock >/var/log/dockerd.log 2>&1 &
for _ in $(seq 1 60); do
    docker info >/dev/null 2>&1 && break
    sleep 0.5
done
if ! docker info >/dev/null 2>&1; then
    echo "agentbox: inner dockerd did not start within 30s" >&2
    tail -30 /var/log/dockerd.log >&2
    exit 1
fi
chmod 666 /var/run/docker.sock
# DOCKER-USER exists once dockerd runs; containers the box starts must not
# reach the metadata forwarder, or every sandbox would hold the devbox role.
iptables -I DOCKER-USER -d "${AGENTBOX_IMDS_FORWARDER%:*}" -p tcp --dport "${AGENTBOX_IMDS_FORWARDER##*:}" -j DROP
# Nor the metadata address itself: on a Google VM the metadata server answers any
# NATed container at 169.254.169.254 with the VM's account token, with no hop
# limit like AWS's IMDSv2 (dispatch://INFRA-993). The box's own processes are not
# in this chain and keep their relay.
iptables -I DOCKER-USER -d 169.254.169.254 -j DROP

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


# An ssh config for the other machines a box talks to (agentbox/box-ssh-config): `ssh
# sami-rocqperf` from a box connected as ubuntu on port 22 and was refused, since the box has the
# key and the known_hosts file but no entry naming the user, the port and those files (the cost
# lead's box on sami-devbox, 2026-11-03 15:04Z).
/usr/local/bin/box-ssh-config "$HOME" "$AGENTBOX_USER" || \
    echo "agentbox: no ssh config written; \`ssh sami-rocqperf\` from this box may be refused" >&2

# The session runs as the mounted directories' owner with the launcher's
# environment intact (setpriv keeps env unless told otherwise).
exec setpriv --reuid="$AGENTBOX_USER" --regid="$AGENTBOX_USER" --init-groups "$@"
