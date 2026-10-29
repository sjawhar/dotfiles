#!/bin/bash
# Arm the unit that keeps this box's shared model-gateway identity token in tmpfs
# (scripts/ossv-box-gateway-token, dispatch://INFRA-481).
#
# This installer is separate from the key timer during the credential cutover.
# Run it only after the shared dev-box account and its Token Creator grants are
# live, then switch the local provider to read gateway-token before retiring the
# legacy key timer.
# The gateway must already accept the `model-gateway-gce` identity token and
# map `dev-box` to its team. The token timer does not add gateway authentication.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ "$(hostname -s)" = sami-rocqperf ] || {
    echo "ossv-box-gateway-token: only sami-rocqperf has this developer-box route" >&2
    exit 1
}
command -v gcloud > /dev/null || {
    echo "ossv-box-gateway-token: gcloud is not available" >&2
    exit 1
}

arm_user_timer omp ossv-box-gateway-token
