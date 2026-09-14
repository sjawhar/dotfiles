#!/bin/bash
set -euo pipefail

# Marks this machine as one where personal agent sessions run in boxes
# (scripts/agentbox, devpod/Dockerfile): shims/omp refuses to start on the host
# here and names `agentbox omp <repo>`. Machine-local role installer, like
# forward.sh and omp-embed.sh: not sourced by install.sh, because the laptop
# and other hosts without the Sysbox runtime keep running omp directly. Run it
# once on a host after installing Sysbox (sysbox-runc registered with Docker).
#
#   installers/agentbox.sh          enable: verify the runtime, create the network, write the marker
#   installers/agentbox.sh disable  remove the marker; omp runs on the host again

MARKER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/agentbox"
MARKER="$MARKER_DIR/host"

case "${1:-enable}" in
    enable)
        command -v docker >/dev/null || { echo "agentbox: docker is required (installers/docker.sh)" >&2; exit 1; }
        if ! docker info --format '{{range $k, $v := .Runtimes}}{{$k}} {{end}}' | grep -qw sysbox-runc; then
            echo "agentbox: Docker has no sysbox-runc runtime; install Sysbox (https://github.com/nestybox/sysbox) first" >&2
            exit 1
        fi
        docker network inspect agentbox >/dev/null 2>&1 || docker network create agentbox >/dev/null
        mkdir -p "$MARKER_DIR"
        printf 'personal agent sessions run in agentboxes on this host; written by installers/agentbox.sh\n' > "$MARKER"
        echo "agentbox: enabled ($MARKER); omp on the host now refuses outside a box"
        ;;
    disable)
        rm -f "$MARKER"
        echo "agentbox: disabled; omp runs on the host again"
        ;;
    *)
        echo "usage: $(basename "$0") [enable|disable]" >&2
        exit 2
        ;;
esac
