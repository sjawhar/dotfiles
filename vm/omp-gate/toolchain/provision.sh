#!/usr/bin/env bash
# Build /data/toolchain on a gate VM from nothing. Idempotent: it never
# overwrites an installed bun or an addon file that is already there.
#
# On a fresh VM, copy this directory's control files and the addon over, then run:
#   scp -r provision.sh install-bun.sh install-rust.sh README.md MANIFEST.md \
#       omp-toolchain-prepare natives-<commit>.node  <vm>:/tmp/tc/
#   ssh <vm> 'cd /tmp/tc && ./provision.sh 1.4.4 /tmp/tc/natives-<commit>.node'
#
# Usage: provision.sh [bun-version] [addon-file]
#   bun-version  default 1.4.4 — the floor upstream main needs
#   addon-file   default $HOME/natives-553f0d10.node, named pi_natives-<tag>.node
#                in the toolchain, where <tag> is the part after "natives-"
# It also runs install-rust.sh, which installs the Rust toolchain the repo's
# rust-toolchain.toml pins; that script carries the Rust version.
set -euo pipefail

TOOLCHAIN=${TOOLCHAIN:-/data/toolchain}
BUN_VERSION=${1:-1.4.4}
ADDON_SRC=${2:-$HOME/natives-553f0d10.node}
HERE=$(cd "$(dirname "$0")" && pwd)

[ -f "$ADDON_SRC" ] || { echo "addon not found: $ADDON_SRC" >&2; exit 1; }

install -d "$TOOLCHAIN/bin" "$TOOLCHAIN/natives" "$TOOLCHAIN/bun"

# --- bun, from the official release, checksum-verified -----------------------
if [ ! -x "$TOOLCHAIN/bun/$BUN_VERSION/bin/bun" ]; then
	ASSET=bun-linux-x64 # AVX2 build; use bun-linux-x64-baseline on a CPU without avx2
	grep -q ' avx2 ' /proc/cpuinfo || ASSET=bun-linux-x64-baseline
	BASE="https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}"
	TMP=$(mktemp -d "${TMPDIR:-/data/tmp}/bun-install-XXXXXX")
	trap 'rm -rf "$TMP"' EXIT
	(
		cd "$TMP"
		curl -fsSL -O "$BASE/SHASUMS256.txt"
		curl -fsSL -O "$BASE/${ASSET}.zip"
		grep " ${ASSET}.zip\$" SHASUMS256.txt >expected.txt
		sha256sum -c expected.txt
		unzip -q "${ASSET}.zip"
		install -d "$TOOLCHAIN/bun/$BUN_VERSION/bin"
		install -m 0755 "${ASSET}/bun" "$TOOLCHAIN/bun/$BUN_VERSION/bin/bun"
	)
fi
ln -sfn "../bun/$BUN_VERSION/bin/bun" "$TOOLCHAIN/bin/bun"
ln -sfn "../bun/$BUN_VERSION/bin/bun" "$TOOLCHAIN/bin/bunx"

# --- the prebuilt pi_natives addon ------------------------------------------
TAG=$(basename "$ADDON_SRC"); TAG=${TAG#natives-}; TAG=${TAG%.node}
DEST="$TOOLCHAIN/natives/pi_natives-${TAG}.node"
if [ ! -f "$DEST" ]; then
	cp -p "$ADDON_SRC" "$DEST"
	chmod 0755 "$DEST"
fi
ln -sfn "$(basename "$DEST")" "$TOOLCHAIN/natives/pi_natives.node"

# --- control files (skipped when this script already runs from the toolchain) --
put() { # put <mode> <source> <destination>
	[ -f "$2" ] || return 0
	[ "$2" -ef "$3" ] && return 0
	install -m "$1" "$2" "$3"
}
put 0644 "$HERE/README.md" "$TOOLCHAIN/README.md"
put 0644 "$HERE/MANIFEST.md" "$TOOLCHAIN/MANIFEST.md"
put 0755 "$HERE/install-bun.sh" "$TOOLCHAIN/install-bun.sh"
put 0755 "$HERE/install-rust.sh" "$TOOLCHAIN/install-rust.sh"
put 0755 "$HERE/install-rsync.sh" "$TOOLCHAIN/install-rsync.sh"
put 0755 "$HERE/omp-toolchain-prepare" "$TOOLCHAIN/bin/omp-toolchain-prepare"
put 0755 "$HERE/provision.sh" "$TOOLCHAIN/provision.sh"

# --- the Rust toolchain the repo pins ----------------------------------------
# Its own RUSTUP_HOME and CARGO_HOME under $TOOLCHAIN/rust, shimmed into
# $TOOLCHAIN/bin. The version lives in install-rust.sh and nowhere else.
"$TOOLCHAIN/install-rust.sh"
# The reference rsync those Rust tests speak to (Ubuntu's is too old).
"$TOOLCHAIN/install-rsync.sh"

echo "bun    $("$TOOLCHAIN/bin/bun" --version)  ($TOOLCHAIN/bin/bun)"
echo "addon  $(readlink -f "$TOOLCHAIN/natives/pi_natives.node")"
sha256sum "$TOOLCHAIN/bun/$BUN_VERSION/bin/bun" "$DEST"
