#!/bin/bash
# The Google Cloud SDK at ~/.gcloud/google-cloud-sdk, with gke-gcloud-auth-plugin, and the
# dispatch cluster's kubectl context. Devbox role installer: devbox/install.sh runs it before
# installers/gcloud-box.sh.
#
# Why this path and not the distribution's: agent boxes mount ~/.gcloud read-only
# (agentbox/mounts) and shims/gcloud falls back to it, so one SDK serves host sessions and every
# box. A Google VM's image ships gcloud as a snap under /snap, which no box can see; on
# sami-devbox's first run (2026-11-02) a box answered "real gcloud binary not found". sami-rocqperf
# has 589.0.0 at this path, installed by hand; this installer is that step in code.
#
# The context is the one every box doctor and the build pool use,
# gke_theorem-agent-infra_us-west1_dispatch. `get-credentials` writes it with
# gke-gcloud-auth-plugin, which takes its token from gcloud, so the context carries no credential.
#
# Idempotent: installs only when the pinned version is not there, and writes the context only
# when it is missing.
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# The sha256 of google-cloud-cli-589.0.0-linux-x86_64.tar.gz as downloaded from dl.google.com on
# 2026-11-02; 589.0.0 is the version sami-rocqperf runs.
GCLOUD_SDK_VERSION=589.0.0
GCLOUD_SDK_SHA256=f865da648f6fdae2015abb5b32eaa0eb10520e88f0ca735cfb33795868c8d26f
GCLOUD_SDK_ROOT="$HOME/.gcloud"
GCLOUD_SDK_BIN="$GCLOUD_SDK_ROOT/google-cloud-sdk/bin/gcloud"
KUBE_CONTEXT=gke_theorem-agent-infra_us-west1_dispatch

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

if kubectl config get-contexts -o name 2>/dev/null | grep -qx "$KUBE_CONTEXT"; then
    echo "gcloud-sdk: kubectl context $KUBE_CONTEXT already present"
else
    PATH="$GCLOUD_SDK_ROOT/google-cloud-sdk/bin:$PATH" \
        "$GCLOUD_SDK_BIN" container clusters get-credentials dispatch --region us-west1 --project theorem-agent-infra
fi
