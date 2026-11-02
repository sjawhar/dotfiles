#!/bin/bash
# Arm the unit that keeps this box's shared model-gateway identity token in tmpfs
# (scripts/ossv-box-gateway-token, dispatch://INFRA-481).
#
# This installer is separate from the key timer during the credential cutover.
# Run it only after the shared dev-box account and the box's OpenID Token Creator
# grant on it are live, then switch the local provider to read gateway-token
# before retiring the legacy key timer.
# The gateway must already accept the `model-gateway-gce` identity token and
# map `dev-box` to its team. The token timer does not add gateway authentication.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# sami-devbox (dispatch://INFRA-993) reaches the gateway on tcp:4000 by its tag (agent-infra
# bootstrap/main.go) and has no lane key: the gateway takes only dev-box's token from it.
case "$(hostname -s)" in
    sami-rocqperf | sami-devbox) ;;
    *)
        echo "ossv-box-gateway-token: only sami-rocqperf and sami-devbox have this developer-box route" >&2
        exit 1
        ;;
esac
for tool in curl jq; do
    command -v "$tool" > /dev/null || {
        echo "ossv-box-gateway-token: $tool is not available" >&2
        exit 1
    }
done

arm_user_timer omp ossv-box-gateway-token
