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

VERSION=0.1.0
TAG="theorem-session-v$VERSION"
REPO=theorem-labs/agent-infra
# Release theorem-session-v0.1.0 (its tag is commit d26310a43819), read on 2026-11-22: each
# archive's line in its SHA256SUMS, which GitHub's own digest of the asset agrees with, and the
# sha256 of the theorem-session each archive holds.
SHA256_AMD64=eef2b631b12f554e071e73a7eee3f757339f5387525d7951ba6bca50eeb3a652
SHA256_ARM64=96e1f7b87125d7e586c3bc82d92c66394d0672bc28a8c6aee5fbf32fccee33a2
BINARY_SHA256_AMD64=f1159a3e619e5ba0cd6bcd3b0f92a347b93d26a506d88e31313ed28cae3e9fb1
BINARY_SHA256_ARM64=f4e32e0b3aa7bdf9ee27f396774a3177fb2bef91437004a31212ff6cedec4c3f
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
