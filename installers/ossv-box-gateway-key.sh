#!/bin/bash
# Arm the unit that keeps this box's OSS Verify model-gateway key in tmpfs
# (scripts/ossv-box-gateway-key, dispatch://INFRA-398).
#
# Standalone installer — not sourced by install.sh. Run it on sami-rocqperf, the
# one box that reads the key: the tailnet policy grants that box's two addresses
# tag:model-gateway on tcp:4000 and tcp:22 (theorem-labs/agent-infra#335 and
# #336), and the script reaches the gateway VM as sami's OS Login user.
# sami-devbox signs in to the gateway with the dev-box@ ID token instead
# (installers/ossv-box-gateway-token.sh, dispatch://INFRA-993). On any other
# machine the unit would fail every run.
#
# Why this exists: omp/models.local.yml routes this box's Claude and Sol traffic
# through the gateway and names the key file as `apiKeyFile`; each run writes the
# file, which is in tmpfs and does not survive a reboot, and then writes the key
# into ~/.omp/agent/models.yml (scripts/omp-models-build).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ "$(hostname -s)" = sami-rocqperf ] || {
    echo "ossv-box-gateway-key: only sami-rocqperf reads the box key; sami-devbox uses ossv-box-gateway-token; not arming here" >&2
    exit 1
}
[ -x "$HOME/src/oss-verifier/bin/ossv-vm" ] || {
    echo "ossv-box-gateway-key: no ~/src/oss-verifier/bin/ossv-vm, which the script reads the key through" >&2
    exit 1
}

arm_user_timer omp ossv-box-gateway-key
