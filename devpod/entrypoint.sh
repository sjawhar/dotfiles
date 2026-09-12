#!/bin/bash
# Box startup: bring up the inner Docker daemon, then idle. Sessions enter with
# `docker exec` (scripts/agentbox sh|omp), so nothing else runs here. A daemon
# that does not come up is fatal: the launcher watches for the ready line and
# removes the box when it never appears.
set -euo pipefail

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
# Sessions run as uid 1000 and are in the docker group already; the socket is
# created root:root 660 before the group exists on it, so open it to the box.
chmod 666 /var/run/docker.sock
echo "inner dockerd ready $(docker version --format '{{.Server.Version}}')"
exec sleep infinity
