#!/bin/bash
# Arm the unit that keeps this box's OSS Verify model-gateway key in tmpfs
# (scripts/ossv-box-gateway-key, dispatch://INFRA-398).
#
# Standalone installer — not sourced by install.sh. Two machines read the key:
# - sami-rocqperf, through the gateway VM: the tailnet policy grants that box's two
#   addresses tag:model-gateway on tcp:4000 and tcp:22 (theorem-labs/agent-infra#335
#   and #336), and the script reaches the VM as sami's OS Login user.
# - sami-devbox, itself, as the VM's attached account, once that account may read the
#   secret (dispatch://INFRA-1099). Until then a read answers 403, so this installer
#   prints a WAIT line and arms nothing (a unit that fails every run is noise); run it
#   again after the grant. Its omp needs no key: it signs in to the gateway with the
#   dev-box@ ID token (installers/ossv-box-gateway-token.sh). The key file is for the
#   tools that need the key itself, a Claude Code `apiKeyHelper` among them.
# On any other machine the unit would fail every run.
#
# Why this exists: omp/models.local.yml on sami-rocqperf routes Claude and Sol traffic
# through the gateway and names the key file as `apiKeyFile`; each run writes the
# file, which is in tmpfs and does not survive a reboot, and then writes the key
# into ~/.omp/agent/models.yml (scripts/omp-models-build).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

case "$(hostname -s)" in
    sami-rocqperf)
        [ -x "$HOME/src/oss-verifier/bin/ossv-vm" ] || {
            echo "ossv-box-gateway-key: no ~/src/oss-verifier/bin/ossv-vm, which the script reads the key through" >&2
            exit 1
        }
        ;;
    sami-devbox)
        if ! "$HOME/.dotfiles/scripts/ossv-box-gateway-key"; then
            echo "WAIT ossv-box-gateway-key: this VM's account cannot read agent-secrets-box-model-gateway-key yet (dispatch://INFRA-1099); not arming; run this installer again after the grant" >&2
            exit 0
        fi
        ;;
    *)
        echo "ossv-box-gateway-key: only sami-rocqperf and sami-devbox read the box key; not arming here" >&2
        exit 1
        ;;
esac

arm_user_timer omp ossv-box-gateway-key
