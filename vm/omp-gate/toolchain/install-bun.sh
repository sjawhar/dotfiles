#!/usr/bin/env bash
# Rebuild (or add) a bun version inside /data/toolchain, from the official
# release, checksum-verified against the release's own SHASUMS256.txt.
#
# Usage: install-bun.sh <version> [--make-default]
#   install-bun.sh 1.4.4
#   install-bun.sh 1.4.5 --make-default   # also repoints /data/toolchain/bin/bun
#
# Raising the floor is this one command with --make-default; nothing else in the
# toolchain names a version.
set -euo pipefail

TOOLCHAIN=${TOOLCHAIN:-/data/toolchain}
[ $# -ge 1 ] || { echo "usage: $(basename "$0") <version> [--make-default]" >&2; exit 2; }
VERSION=$1; shift
DEFAULT=0
while [ $# -gt 0 ]; do
	case $1 in
	--make-default) DEFAULT=1 ;;
	*) echo "unknown flag: $1" >&2; exit 2 ;;
	esac
	shift
done

# bun-linux-x64 is the AVX2 ("modern") build; this VM has avx2 (see MANIFEST.md).
ASSET=bun-linux-x64
BASE="https://github.com/oven-sh/bun/releases/download/bun-v${VERSION}"
TMP=$(mktemp -d "${TMPDIR:-/data/tmp}/bun-install-XXXXXX")
trap 'rm -rf "$TMP"' EXIT

cd "$TMP"
curl -fsSL -O "$BASE/SHASUMS256.txt"
curl -fsSL -O "$BASE/${ASSET}.zip"
grep " ${ASSET}.zip\$" SHASUMS256.txt >expected.txt
sha256sum -c expected.txt
unzip -q "${ASSET}.zip"

install -d "$TOOLCHAIN/bun/$VERSION/bin"
install -m 0755 "${ASSET}/bun" "$TOOLCHAIN/bun/$VERSION/bin/bun"
"$TOOLCHAIN/bun/$VERSION/bin/bun" --version

if [ "$DEFAULT" -eq 1 ]; then
	ln -sfn "../bun/$VERSION/bin/bun" "$TOOLCHAIN/bin/bun"
	ln -sfn "../bun/$VERSION/bin/bun" "$TOOLCHAIN/bin/bunx"
	echo "default is now $("$TOOLCHAIN/bin/bun" --version)"
	echo "update $TOOLCHAIN/MANIFEST.md with the new version and checksum"
fi
