#!/bin/bash
set -euo pipefail

# The two box-identity paths that scripts written on sami-rocqperf name, made on a Google VM
# (sami-devbox, dispatch://INFRA-993) so such a script runs there unchanged:
#   /mnt/nvme/<user>/boxid/gcloud-box        a gcloud config dir (CLOUDSDK_CONFIG) whose active
#                                            configuration `box` is the VM's attached account,
#                                            with no stored credential: installers/gcloud-box.sh's
#                                            Google branch, run against this dir;
#   /mnt/nvme/<user>/boxid/infra-kubeconfig  a kubeconfig (KUBECONFIG) with the one context
#                                            gke_theorem-agent-infra_us-west1_dispatch, taken from
#                                            ~/.kube/config (installers/gcloud-sdk.sh writes it);
#                                            it holds no credential: gke-gcloud-auth-plugin takes
#                                            its token from gcloud.
# On sami-rocqperf a person made the same two paths by hand on 2026-10-18, with the Azure
# federation credential in the dir: that copy breaks gcloud on a Google VM and is not copied.
# An agent box reaches them by `--dir /mnt/nvme/<user>/boxid`, and there gcloud reaches the
# metadata server through the box's relay. Devbox role installer: devbox/install.sh runs it
# after gcloud-sdk.sh and gcloud-box.sh. Idempotent; prints each change. Elsewhere (not a
# Google VM, or no /mnt/nvme/<user>) it changes nothing.

here="$(dirname "${BASH_SOURCE[0]}")"
BOXID_DIR="${GCLOUD_BOXID_DIR:-/mnt/nvme/$(id -un)/boxid}"
KUBE_CONTEXT=gke_theorem-agent-infra_us-west1_dispatch

if ! "$here/../scripts/on-google-vm"; then
    echo "gcloud-boxid: not a Google VM; nothing changed."
    exit 0
fi
if [[ ! -d "$(dirname "$BOXID_DIR")" ]]; then
    echo "gcloud-boxid: no $(dirname "$BOXID_DIR"); nothing changed."
    exit 0
fi
[[ -d "$BOXID_DIR" ]] || mkdir -m 700 "$BOXID_DIR"

CLOUDSDK_CONFIG="$BOXID_DIR/gcloud-box" bash "$here/gcloud-box.sh" | sed 's/^gcloud-box:/gcloud-boxid: gcloud-box:/'

new="$(mktemp)"
trap 'rm -f "$new"' EXIT
(umask 077 && kubectl --kubeconfig "$HOME/.kube/config" config view --minify --flatten --context "$KUBE_CONTEXT" >"$new")
if ! cmp -s "$new" "$BOXID_DIR/infra-kubeconfig"; then
    install -m 600 "$new" "$BOXID_DIR/infra-kubeconfig"
    echo "gcloud-boxid: wrote $BOXID_DIR/infra-kubeconfig (context $KUBE_CONTEXT)"
fi
echo "gcloud-boxid: $BOXID_DIR/gcloud-box and $BOXID_DIR/infra-kubeconfig act as the VM's attached account"
