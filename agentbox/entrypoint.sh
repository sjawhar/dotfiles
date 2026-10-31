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

# $HOME in the box is the host user's home PATH, and only the allow-listed paths under it
# are mounts (agentbox/mounts); docker creates every parent those mounts need as a
# root-owned directory of the box's own overlay. The image ships a /home/ubuntu skeleton
# owned by the session user, so on a host whose user is `ubuntu` those parents were the
# user's - but on a host whose user has any other name every one of them was root's, and
# the session could write nothing outside the list: no ~/.docker (so no ghcr login and no
# credHelpers), no ~/.config/gh-agent (so every gh call failed), no ~/.local/state/mise (a
# warning on every mise command), measured on sami-rocqperf 2026-10-31. So give the
# session user every directory of the overlay under $HOME. A mount is skipped by device -
# a bind of a host path has an st_dev of its own - because chowning one would change the
# host's directory; the host's own uid already arrives inside as the session user.
home_dev="$(stat -c %d "$HOME")"
while IFS= read -r -d '' dir; do
    if [[ "$(stat -c %d "$dir")" == "$home_dev" ]]; then
        chown "$AGENTBOX_USER:$AGENTBOX_USER" "$dir"
    fi
done < <(find "$HOME" -xdev -type d -print0)

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

# Private agent-c images: the inner docker logs into ghcr.io with the
# platform's read-only packages token (agent-tier GHCR_PULL_TOKEN), as the
# session user so the secrets client finds its age key and the login lands in
# the box's own ~/.docker (not mounted from the host; it dies with the box).
# A failed login is reported, not fatal: public pulls still work.
# Every step below runs from `/` (`env -C`), never the box's working directory: a mise shim on
# PATH reads the tool files of whatever directory it runs in, so from a checkout it goes to
# GitHub to resolve that tree's toolchains - and MISE_DATA_DIR, which points at the host's
# store where the pinned tools already are, must be passed or mise looks in an empty default
# and installs from the network too. On 2026-10-31 that cost a box its Docker Hub credential
# helpers: `jq` in the block below ran without MISE_DATA_DIR, mise tried to resolve jq and the
# workspace's rust-toolchain.toml anonymously, GitHub answered 403 rate-limited, and the box
# started with an empty ~/.docker/config.json and a warning nobody was reading.
# shellcheck disable=SC2016  # the inner sh expands $GHCR_PULL_TOKEN; this shell must not
if ! setpriv --reuid="$AGENTBOX_USER" --regid="$AGENTBOX_USER" --init-groups --reset-env \
        env -C / HOME="$HOME" PATH="$PATH" MISE_DATA_DIR="${MISE_DATA_DIR:-}" \
        sh -c 'secrets GHCR_PULL_TOKEN -- sh -c "printf %s \"\$GHCR_PULL_TOKEN\" | docker login ghcr.io -u trajectory-labs-pbc --password-stdin" >/dev/null 2>&1'; then
    echo "agentbox: ghcr.io login failed; private image pulls will fail until it is fixed" >&2
fi

# Docker Hub and dhi.io authenticate through scripts/docker-credential-secretsd,
# which reads the org's pull-only token from the secrets store when docker asks,
# so no token for them sits in the box. Merged into the box's config beside the
# ghcr login; `agentbox doctor` checks the helper answers, because a config that
# names a missing helper fails every Docker Hub pull, public images included.
# shellcheck disable=SC2016  # the inner sh expands $HOME and $HELPERS; this shell must not
if ! setpriv --reuid="$AGENTBOX_USER" --regid="$AGENTBOX_USER" --init-groups --reset-env \
        env -C / HOME="$HOME" PATH="$PATH" MISE_DATA_DIR="${MISE_DATA_DIR:-}" \
        HELPERS='{"dhi.io": "secretsd", "https://index.docker.io/v1/": "secretsd"}' \
        sh -c 'c="$HOME/.docker/config.json"; mkdir -p "$HOME/.docker" && { [ -s "$c" ] || echo "{}" > "$c"; } && jq --argjson h "$HELPERS" ".credHelpers = ((.credHelpers // {}) + \$h)" "$c" > "$c.tmp" && mv "$c.tmp" "$c"'; then
    echo "agentbox: docker credHelpers not written; Docker Hub pulls stay anonymous" >&2
fi

# ECR needs no token in the box: docker-credential-ecr-login exchanges the box's instance role
# for a registry token when docker asks. The identity is already allowed — what was missing is
# that docker only reaches it when the config NAMES the helper for that host. Measured 2026-09-26,
# same box and role ten seconds apart: without the entry `docker manifest inspect` returns "no
# basic auth credentials" (docker sent nothing); with it, the call authenticates as the instance
# role and any refusal after that is an IAM answer. The host list comes from the machine's own
# config via AGENTBOX_ECR_HELPERS (scripts/agentbox), so a box mirrors the host rather than
# carrying a second list that can drift. Skipped, with a note, when the helper is not installed:
# naming a missing helper breaks every pull for that host, which is worse than no entry.
# shellcheck disable=SC2016  # the inner sh expands $HOME and $ECR_HELPERS; this shell must not
if [ -n "${AGENTBOX_ECR_HELPERS:-}" ] && [ "$AGENTBOX_ECR_HELPERS" != "{}" ]; then
    if ! setpriv --reuid="$AGENTBOX_USER" --regid="$AGENTBOX_USER" --init-groups --reset-env \
            env -C / HOME="$HOME" PATH="$PATH" MISE_DATA_DIR="${MISE_DATA_DIR:-}" \
            ECR_HELPERS="$AGENTBOX_ECR_HELPERS" \
            sh -c 'command -v docker-credential-ecr-login >/dev/null 2>&1 || exit 7; c="$HOME/.docker/config.json"; mkdir -p "$HOME/.docker" && { [ -s "$c" ] || echo "{}" > "$c"; } && jq --argjson h "$ECR_HELPERS" ".credHelpers = ((.credHelpers // {}) + \$h)" "$c" > "$c.tmp" && mv "$c.tmp" "$c"'; then
        echo "agentbox: ECR credHelpers not written; ECR pulls will fail with 'no basic auth credentials'" >&2
    fi
fi

# The session runs as the mounted directories' owner with the launcher's
# environment intact (setpriv keeps env unless told otherwise).
exec setpriv --reuid="$AGENTBOX_USER" --regid="$AGENTBOX_USER" --init-groups "$@"
