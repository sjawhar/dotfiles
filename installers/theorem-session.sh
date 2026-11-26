#!/bin/bash
# theorem-session, agent-infra's command that starts, lists, opens and ends agent sessions on the
# dispatch cluster (theorem-labs/agent-infra sessions/), from its pinned GitHub release, checked by
# SHA-256, at ~/.local/bin/theorem-session: on PATH in a host shell and in every agent box
# (agentbox/mounts binds ~/.local/bin). Devbox role installer: devbox/install.sh runs it.
#
# theorem-labs/agent-infra is private, so the download needs a GitHub token: an installation token
# of the GitHub App for owner theorem-labs, narrowed to agent-infra and read access, that
# shims/gh-app-token mints for profile agent through the secrets broker (installers/agent-secrets.sh).
#
# The archive is checked against the SHA-256 pinned here, not against the release's own SHA256SUMS:
# anyone who can write to agent-infra can replace a release's assets and its SHA256SUMS together.
# The theorem-session inside is checked against its own pinned SHA-256 too, and that digest is how
# a later run knows the installed binary is this release (theorem-session has no --version): a run
# that finds it downloads nothing and changes nothing. A mismatch fails the run and leaves the
# installed binary as it was.
#
# To upgrade: set VERSION, each SHA256_<arch> to that archive's line in the new release's
# SHA256SUMS, and each BINARY_SHA256_<arch> to the sha256sum of the theorem-session that archive
# holds. To roll back, set them to an older release's values and run this again.
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

VERSION=0.2.1
TAG="theorem-session-v$VERSION"
REPO=theorem-labs/agent-infra
# Release theorem-session-v0.2.1 (its tag is commit b4f68a388d5), read on 2026-11-26: each
# archive's line in its SHA256SUMS, which GitHub's own digest of the asset agrees with, and the
# sha256 of the theorem-session each archive holds.
SHA256_AMD64=e4d660db1cacedc8d4712777946bafb83fa4b35d8585bcd942fe0792478d5205
SHA256_ARM64=f53175e95281a1760ae4e81a2c237c3beb93bf20db271268ce52d9e455d7a5bd
BINARY_SHA256_AMD64=1fe30cf972450106aca7976e70ec3b475649be65eef45c4f9207c5ff834dbbba
BINARY_SHA256_ARM64=894e870ed50ff2d41f7910f8d5ff525ce35e80ce3635e4c34453f5ec58ecc85d
BIN_DIR="$HOME/.local/bin"
BIN="$BIN_DIR/theorem-session"

platform="$(uname -s) $(uname -m)"
case "$platform" in
    "Linux x86_64") arch=amd64 expected=$SHA256_AMD64 binary=$BINARY_SHA256_AMD64 ;;
    "Linux aarch64" | "Linux arm64") arch=arm64 expected=$SHA256_ARM64 binary=$BINARY_SHA256_ARM64 ;;
    *)
        echo "theorem-session: this installer pins no $TAG build for $platform; installed nothing" >&2
        exit 1
        ;;
esac

# The SHA-256 of file $1, or nothing when it cannot be read.
sha256() {
    local out
    out="$(sha256sum -- "$1" 2>/dev/null)" || return 0
    printf '%s\n' "${out%% *}"
}

# A link is replaced by the file even when its target matches: this path is the installer's own.
installed=""
if [[ -f "$BIN" && ! -L "$BIN" ]]; then
    installed="$(sha256 "$BIN")"
fi
if [[ "$installed" == "$binary" && -x "$BIN" ]]; then
    echo "theorem-session: $BIN is $TAG already"
    exit 0
fi

asset="theorem-session-linux-$arch.tar.gz"
new=""
tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "$tmp_dir" ${new:+"$new"}' EXIT
if ! token="$(gh-app-token agent --owner theorem-labs --repo agent-infra --read-only)" || [[ -z "$token" ]]; then
    echo "theorem-session: gh-app-token minted no token to read $REPO (above); installed nothing" >&2
    exit 1
fi
if ! GH_TOKEN="$token" gh release download "$TAG" --repo "$REPO" --pattern "$asset" --dir "$tmp_dir"; then
    echo "theorem-session: gh release download $TAG --repo $REPO --pattern $asset failed (above); installed nothing" >&2
    exit 1
fi
actual="$(sha256 "$tmp_dir/$asset")"
if [[ "$actual" != "$expected" ]]; then
    echo "theorem-session: SHA-256 of $asset is ${actual:-unreadable}, expected $expected; installed nothing" >&2
    exit 1
fi
if ! tar -xzf "$tmp_dir/$asset" -C "$tmp_dir" theorem-session; then
    echo "theorem-session: $asset holds no theorem-session at its top level (above); installed nothing" >&2
    exit 1
fi
actual="$(sha256 "$tmp_dir/theorem-session")"
if [[ "$actual" != "$binary" ]]; then
    echo "theorem-session: SHA-256 of theorem-session in $asset is ${actual:-unreadable}, expected $binary; installed nothing" >&2
    exit 1
fi

# Written beside the binary and renamed over it, so no caller ever runs a part-written file; -T
# fails on a directory at that path instead of moving the file into it.
mkdir -p "$BIN_DIR"
new="$(mktemp "$BIN_DIR/.theorem-session.XXXXXX")"
cp -- "$tmp_dir/theorem-session" "$new"
chmod 0755 "$new"
mv -fT -- "$new" "$BIN"
new=""
if [[ "$(sha256 "$BIN")" != "$binary" ]]; then
    echo "theorem-session: $BIN is not $TAG's binary after the install" >&2
    exit 1
fi
echo "theorem-session: installed $TAG ($asset) at $BIN${installed:+, replacing a binary with SHA-256 $installed}"
