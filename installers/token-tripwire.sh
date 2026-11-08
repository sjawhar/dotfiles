#!/bin/bash
# Arm the secretsd token tripwire (scripts/token-tripwire).
#
# Standalone installer (like envoy-role-holder-watch.sh) — not sourced by install.sh.
# Run it on a machine that runs boxed omp sessions: the watch follows THIS machine's secretsd
# journal and reads THIS machine's /proc and docker to name the box that failed, so there is
# nothing to arm on a laptop that only SSHes in.
#
# Why this exists: on 2026-11-07 a session-interview script resumed a session copy under the
# live session's own id, and secretsd keys its registry on that id alone, so the copy displaced
# the live token and its exit removed it. 59 of 153 live box sessions were left unable to fetch
# a secret, silently, and nothing noticed for a day. That script is gone, but nothing watches
# for a session whose token is not known, and the state
# cannot be enumerated: secretsd has no registry dump and the failing log line carries no
# session id. A tripwire is the only instrument that can name it. (dispatch://INFRA-1644)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null || {
    echo "token-tripwire: python3 not on PATH; the watch is a python3 script" >&2
    exit 1
}
[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json" ] || {
    echo "token-tripwire: no envoy.json; the watch has no listener to send its notices through" >&2
    exit 1
}
command -v docker >/dev/null || {
    echo "token-tripwire: no docker; a failing pid could not be resolved to its box" >&2
    exit 1
}

arm_user_service omp token-tripwire
