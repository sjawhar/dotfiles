#!/bin/bash
# Box startup: the container's main process is the session command itself
# (scripts/agentbox passes shims/omp as the docker run command), so the box
# lives exactly as long as the session and `--rm` cleans it up. No dockerd
# here: the inner daemon starts lazily on the first `docker` call, via the
# /usr/local/bin/docker wrapper and /usr/local/libexec/agentbox-dockerd.
# The forwarder address is validated up front because the dockerd firewall
# rule depends on it: the launcher always passes it, so a missing one is a
# launcher bug, not a box that silently starts sandboxes with role access.
set -euo pipefail

[[ -n "${AGENTBOX_IMDS_FORWARDER:-}" ]] || { echo "agentbox: AGENTBOX_IMDS_FORWARDER is not set" >&2; exit 1; }
exec "$@"
