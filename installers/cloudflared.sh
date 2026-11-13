#!/bin/bash
# Install Cloudflare Access's SSH client at the same pin as dojo's
# platform/oryx-edge/deploy/install-cloudflared.sh.
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

VERSION=2026.10.7
KEY_FPR=CC94B39C77AE7342A68B89628A682D308D4E5E73
KEYRING=/usr/share/keyrings/cloudflare-main.gpg
SOURCE_LIST=/etc/apt/sources.list.d/cloudflared.list
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

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o "$tmp"

fingerprint="$(gpg --show-keys --with-colons "$tmp" | while IFS=: read -r kind _ _ _ _ _ _ _ _ value _; do
    if [[ "$kind" == fpr ]]; then
        printf '%s\n' "$value"
        break
    fi
done)"
if [[ "$fingerprint" != "$KEY_FPR" ]]; then
    echo "cloudflared: apt key fingerprint does not match the pinned Cloudflare key; stop" >&2
    exit 1
fi

keyring="${INSTALL_ROOT}${KEYRING}"
source_list="${INSTALL_ROOT}${SOURCE_LIST}"
preferences="${INSTALL_ROOT}${PREFERENCES}"
sudo mkdir -p "$(dirname "$keyring")" "$(dirname "$source_list")" "$(dirname "$preferences")"
sudo install -m 0644 "$tmp" "$keyring"
printf 'deb [signed-by=%s] https://pkg.cloudflare.com/cloudflared any main\n' "$KEYRING" \
    | sudo tee "$source_list" >/dev/null
sudo tee "$preferences" >/dev/null <<EOF
# Pinned by installers/cloudflared.sh; change VERSION there to upgrade.
Package: cloudflared
Pin: version $VERSION
Pin-Priority: 1001
EOF

sudo apt-get update -o Dir::Etc::sourcelist=sources.list.d/cloudflared.list \
    -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0
sudo apt-get install -y --allow-downgrades "cloudflared=$VERSION"

if ! has_pinned_version; then
    echo "cloudflared: installed version is not $VERSION" >&2
    exit 1
fi
