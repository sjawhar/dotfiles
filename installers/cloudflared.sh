#!/bin/bash
# Install Cloudflare Access's SSH client from its pinned GitHub release file, checked by SHA-256,
# at the same version as dojo's platform/oryx-edge/deploy/install-cloudflared.sh.
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

VERSION=2026.10.7
PREFERENCES=/etc/apt/preferences.d/cloudflared.pref
INSTALL_ROOT="${INSTALL_ROOT:-}"

has_pinned_version() {
    local version
    version="$(cloudflared --version 2>/dev/null || true)"
    [[ "$version" =~ (^|[[:space:]])${VERSION}([[:space:]]|$) ]]
}

if has_pinned_version; then
    exit 0
fi

# Published in https://github.com/cloudflare/cloudflared/releases/tag/2026.10.7.
SHA256_AMD64=70b1a1e0f46033ffc8d90b406e554044ba6cbf2e4360295ef1163aed31e09393
SHA256_ARM64=d9407eb6e70ac31f54978a6b408a0320b51b664a6d0b498248692d7ac56bf836

arch="$(dpkg --print-architecture)"
case "$arch" in
    amd64) expected=$SHA256_AMD64 ;;
    arm64) expected=$SHA256_ARM64 ;;
    *)
        echo "cloudflared: no release file for architecture $arch; stop" >&2
        exit 1
        ;;
esac

tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "$tmp_dir"' EXIT
deb="$tmp_dir/cloudflared-linux-$arch.deb"
curl -fsSL --retry 3 -o "$deb" "https://github.com/cloudflare/cloudflared/releases/download/$VERSION/cloudflared-linux-$arch.deb"
actual="$(sha256sum "$deb" | cut -d' ' -f1)"
if [[ "$actual" != "$expected" ]]; then
    echo "cloudflared: SHA-256 of cloudflared-linux-$arch.deb is $actual, expected $expected; installed nothing" >&2
    exit 1
fi

preferences="${INSTALL_ROOT}${PREFERENCES}"
sudo mkdir -p "$(dirname "$preferences")"
sudo tee "$preferences" >/dev/null <<EOF
# Pinned by installers/cloudflared.sh; to upgrade, change VERSION, SHA256_AMD64 and SHA256_ARM64 there.
Package: cloudflared
Pin: version $VERSION
Pin-Priority: 1001
EOF
sudo chmod 0644 "$preferences"

# Use dpkg, not apt-get: apt could use a same-version configured cloudflared instead of this checked file.
# This release has no dependencies.
sudo dpkg -i "$deb"

if ! has_pinned_version; then
    echo "cloudflared: installed version is not $VERSION" >&2
    exit 1
fi
