#!/bin/bash
# The Google Cloud SDK at ~/.gcloud/google-cloud-sdk, with gke-gcloud-auth-plugin, and a kubectl
# context for each GKE cluster in KUBE_CLUSTERS. Devbox role installer: devbox/install.sh runs it
# before installers/gcloud-box.sh.
#
# Why this path and not the distribution's: agent boxes mount ~/.gcloud read-only
# (agentbox/mounts) and shims/gcloud falls back to it, so one SDK serves host sessions and every
# box. A Google VM's image ships gcloud as a snap under /snap, which no box can see; on
# sami-devbox's first run (2026-11-02) a box answered "real gcloud binary not found". sami-rocqperf
# has 589.0.0 at this path, installed by hand; this installer is that step in code.
#
# KUBE_CLUSTERS is the one list of the clusters this machine's sessions reach, as `<cluster>
# <location>` in project theorem-agent-infra; each gets context gke_theorem-agent-infra_<location>_
# <cluster>. `get-credentials` writes it with gke-gcloud-auth-plugin, which takes its token from
# gcloud, so a context carries no credential. It also makes the context it writes the current one,
# and every session on the machine shares ~/.kube/config (shims/gcloud, route_get_credentials_
# kubeconfig: on 2026-11-02 a switch to grade-us-west1 failed every session's builds-* Jobs). So it
# writes into a file of its own, and that file is merged in behind the shared one, whose current
# context wins, and renamed over it. A file with no current context gets dispatch's, the context
# every box doctor and the build pool use without naming one.
#
# Idempotent: installs only when the pinned version is not there, and writes a context only when
# it is missing.
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# The sha256 of google-cloud-cli-589.0.0-linux-x86_64.tar.gz as downloaded from dl.google.com on
# 2026-11-02; 589.0.0 is the version sami-rocqperf runs.
GCLOUD_SDK_VERSION=589.0.0
GCLOUD_SDK_SHA256=f865da648f6fdae2015abb5b32eaa0eb10520e88f0ca735cfb33795868c8d26f
GCLOUD_SDK_ROOT="$HOME/.gcloud"
GCLOUD_SDK_BIN="$GCLOUD_SDK_ROOT/google-cloud-sdk/bin/gcloud"
KUBE_PROJECT=theorem-agent-infra
# dispatch: box doctors and the build pool (namespaces builds-*). grade-us-west1: the grade Jobs
# of S3 (namespace grade). sami-rocqperf's ~/.kube/config on 2026-11-03 had these two and three
# stockprobe-gke-* contexts, whose clusters no longer exist (`gcloud container clusters list`).
KUBE_CLUSTERS=(
    "dispatch us-west1"
    "grade-us-west1 us-west1-b"
)
KUBE_DEFAULT_CONTEXT="gke_${KUBE_PROJECT}_us-west1_dispatch"

if [[ -x "$GCLOUD_SDK_BIN" && "$("$GCLOUD_SDK_BIN" version --format='value("Google Cloud SDK")' 2>/dev/null)" == "$GCLOUD_SDK_VERSION" ]]; then
    echo "gcloud-sdk: $GCLOUD_SDK_VERSION already at $GCLOUD_SDK_ROOT"
else
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    tarball="$tmp/google-cloud-cli-${GCLOUD_SDK_VERSION}-linux-x86_64.tar.gz"
    curl -fsSL --retry 3 -o "$tarball" \
        "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-${GCLOUD_SDK_VERSION}-linux-x86_64.tar.gz"
    echo "$GCLOUD_SDK_SHA256  $tarball" | sha256sum -c --quiet -
    mkdir -p "$GCLOUD_SDK_ROOT"
    rm -rf "$GCLOUD_SDK_ROOT/google-cloud-sdk"
    tar -xzf "$tarball" -C "$GCLOUD_SDK_ROOT"
    echo "gcloud-sdk: installed $GCLOUD_SDK_VERSION at $GCLOUD_SDK_ROOT"
fi

# The SDK's own installer is not run, so nothing edits .bashrc; the components command needs no
# prompt with --quiet.
if [[ ! -x "$GCLOUD_SDK_ROOT/google-cloud-sdk/bin/gke-gcloud-auth-plugin" ]]; then
    "$GCLOUD_SDK_BIN" components install gke-gcloud-auth-plugin --quiet
fi
# kubectl finds the plugin on PATH, and the SDK's bin is not on PATH (in a box or out); the
# link is what agentbox/mounts' `.gcloud` entry relies on.
mkdir -p "$HOME/.local/bin"
ln -sfn "$GCLOUD_SDK_ROOT/google-cloud-sdk/bin/gke-gcloud-auth-plugin" "$HOME/.local/bin/gke-gcloud-auth-plugin"

kubeconfig="${KUBECONFIG:-$HOME/.kube/config}"
contexts="$(kubectl config get-contexts -o name 2>/dev/null || true)"
for entry in "${KUBE_CLUSTERS[@]}"; do
    read -r cluster location <<<"$entry"
    context="gke_${KUBE_PROJECT}_${location}_${cluster}"
    if grep -qxF "$context" <<<"$contexts"; then
        echo "gcloud-sdk: kubectl context $context already present"
        continue
    fi
    # A path that does not exist yet: given an empty file, gcloud warns and leaves a backup of it.
    one="$(mktemp -d)"
    KUBECONFIG="$one/config" PATH="$GCLOUD_SDK_ROOT/google-cloud-sdk/bin:$PATH" \
        "$GCLOUD_SDK_BIN" container clusters get-credentials "$cluster" --location "$location" --project "$KUBE_PROJECT"
    mkdir -p "$(dirname "$kubeconfig")"
    (umask 077 && KUBECONFIG="$kubeconfig:$one/config" kubectl config view --flatten >"$kubeconfig.gcloud-sdk.new")
    mv -f "$kubeconfig.gcloud-sdk.new" "$kubeconfig"
    rm -rf "$one"
    echo "gcloud-sdk: kubectl context $context added to $kubeconfig"
done
if [[ -z "$(KUBECONFIG="$kubeconfig" kubectl config current-context 2>/dev/null || true)" ]]; then
    KUBECONFIG="$kubeconfig" kubectl config use-context "$KUBE_DEFAULT_CONTEXT" >/dev/null
    echo "gcloud-sdk: kubectl current context $KUBE_DEFAULT_CONTEXT"
fi
