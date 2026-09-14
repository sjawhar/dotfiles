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
# The box's own processes reach the EC2 metadata forwarder on the host
# (AWS_EC2_METADATA_SERVICE_ENDPOINT); containers the box starts must not, or a
# sandbox running task content would hold the instance role. Container traffic
# leaves through FORWARD, where Docker consults DOCKER-USER first; the box's
# processes use OUTPUT and are unaffected. The launcher always passes the
# address, so a missing one is a launcher bug, not a box without a rule.
[[ -n "${AGENTBOX_IMDS_FORWARDER:-}" ]] || { echo "agentbox: AGENTBOX_IMDS_FORWARDER is not set" >&2; exit 1; }
iptables -I DOCKER-USER -d "${AGENTBOX_IMDS_FORWARDER%:*}" -p tcp --dport "${AGENTBOX_IMDS_FORWARDER##*:}" -j DROP
echo "inner dockerd ready $(docker version --format '{{.Server.Version}}')"
exec sleep infinity
