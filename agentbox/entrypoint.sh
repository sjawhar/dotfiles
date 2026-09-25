#!/bin/bash
# Box startup, as root: bring up the inner dockerd, keep the containers it
# will run away from the host's metadata forwarder, log the inner docker into
# ghcr.io as ubuntu, then hand off to the session command as ubuntu. The
# session command is the container's main process (scripts/agentbox passes
# shims/omp), so the box lives exactly as long as the session and `--rm`
# cleans it up; the daemon dies with it.
set -euo pipefail

# The forwarder address is required because the firewall rule depends on it:
# the launcher always passes it, so a missing one is a launcher bug, not a
# box that silently starts sandboxes with the instance role's access.
[[ -n "${AGENTBOX_IMDS_FORWARDER:-}" ]] || { echo "agentbox: AGENTBOX_IMDS_FORWARDER is not set" >&2; exit 1; }
[[ -n "${AGENTBOX_USER:-}" ]] || { echo "agentbox: AGENTBOX_USER is not set" >&2; exit 1; }

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

# Private agent-c images: the inner docker logs into ghcr.io with the
# platform's read-only packages token (agent-tier GHCR_PULL_TOKEN), as the
# session user so the secrets client finds its age key and the login lands in
# the box's own ~/.docker (not mounted from the host; it dies with the box).
# A failed login is reported, not fatal: public pulls still work.
# shellcheck disable=SC2016  # the inner sh expands $GHCR_PULL_TOKEN; this shell must not
if ! setpriv --reuid="$AGENTBOX_USER" --regid="$AGENTBOX_USER" --init-groups --reset-env \
        env HOME="$HOME" PATH="$PATH" MISE_DATA_DIR="${MISE_DATA_DIR:-}" \
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
        env HOME="$HOME" PATH="$PATH" \
        HELPERS='{"dhi.io": "secretsd", "https://index.docker.io/v1/": "secretsd"}' \
        sh -c 'c="$HOME/.docker/config.json"; mkdir -p "$HOME/.docker" && { [ -s "$c" ] || echo "{}" > "$c"; } && jq --argjson h "$HELPERS" ".credHelpers = ((.credHelpers // {}) + \$h)" "$c" > "$c.tmp" && mv "$c.tmp" "$c"'; then
    echo "agentbox: docker credHelpers not written; Docker Hub pulls stay anonymous" >&2
fi

# The session runs as the mounted directories' owner with the launcher's
# environment intact (setpriv keeps env unless told otherwise).
exec setpriv --reuid="$AGENTBOX_USER" --regid="$AGENTBOX_USER" --init-groups "$@"
