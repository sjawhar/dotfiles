#!/bin/bash
# Arm the unit that keeps this box's OSS Verify model-gateway key in tmpfs
# (scripts/ossv-box-gateway-key, dispatch://INFRA-398).
#
# Standalone installer — not sourced by install.sh. Run it on sami-rocqperf or
# sami-devbox: the script reads the key through the gateway VM, which it reaches
# over ssh on the tailnet with bin/ossv-vm (sami-rocqperf: the policy grants its two
# addresses tag:model-gateway on tcp:4000 and tcp:22, theorem-labs/agent-infra#335
# and #336; sami-devbox: the ossv-vm key the secrets broker installs). sami-devbox's
# omp signs in to the gateway with the dev-box@ ID token instead
# (installers/ossv-box-gateway-token.sh, dispatch://INFRA-993); the key file there is
# for tools that need the key itself, a Claude Code `apiKeyHelper` among them. On any
# other machine the unit would fail every run.
#
# Why this exists: omp/models.local.yml on sami-rocqperf routes Claude and Sol traffic
# through the gateway and names the key file as `apiKeyFile`; each run writes the
# file, which is in tmpfs and does not survive a reboot, and then writes the key
# into ~/.omp/agent/models.yml (scripts/omp-models-build).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

case "$(hostname -s)" in
    sami-rocqperf | sami-devbox) ;;
    *)
        echo "ossv-box-gateway-key: only sami-rocqperf and sami-devbox read the box key; not arming here" >&2
        exit 1
        ;;
esac
[ -x "$HOME/src/oss-verifier/bin/ossv-vm" ] || {
    echo "ossv-box-gateway-key: no ~/src/oss-verifier/bin/ossv-vm, which the script reads the key through" >&2
    exit 1
}

arm_user_timer omp ossv-box-gateway-key
