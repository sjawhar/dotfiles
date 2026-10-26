#!/bin/bash
# Arm the unit that keeps this box's OSS Verify model-gateway key in tmpfs
# (scripts/ossv-box-gateway-key, dispatch://INFRA-398).
#
# Standalone installer — not sourced by install.sh. Run it on sami-rocqperf, the
# one box that reaches the gateway: the tailnet policy grants that box's two
# addresses tag:model-gateway on tcp:4000 and tcp:22, and no other device
# (theorem-labs/agent-infra#335 and #336). On any other machine the unit would
# fail every run.
#
# Why this exists: omp/models.local.yml routes this box's Claude traffic through
# the gateway and reads the key with `!cat`, so a session that starts without
# the file reaches no model. The file is in tmpfs and does not survive a reboot.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ "$(hostname -s)" = sami-rocqperf ] || {
    echo "ossv-box-gateway-key: only sami-rocqperf reaches the model gateway; not arming here" >&2
    exit 1
}
[ -x "$HOME/src/oss-verifier/bin/ossv-vm" ] || {
    echo "ossv-box-gateway-key: no ~/src/oss-verifier/bin/ossv-vm, which the script reads the key through" >&2
    exit 1
}

arm_user_timer omp ossv-box-gateway-key
