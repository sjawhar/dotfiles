#!/usr/bin/env bash
# Build the reference rsync that oh-my-pi's Rust tests speak to, inside
# /data/toolchain. Idempotent: it builds nothing when the version is there.
#
# Why: pi-builtins' rsync tests run the rsync found on PATH as the reference
# peer and assert protocol 33 (crates/pi-builtins/src/rsync/daemon.rs). Ubuntu
# 24.04 ships rsync 3.2.7, protocol 31, so `bun run test:rs` fails two tests
# (rsync::daemon::tests::live_reference_daemon_listing_and_auth and
# rsync::session::tests::batch_read_by_reference_rsync) on a stock box. rsync
# 3.5.1 is the first release with protocol 33.
#
# Usage: install-rsync.sh [version]
#   The version below is the only place the toolchain names an rsync version.
set -euo pipefail

VERSION=3.5.1
SHA256=c55f9c9dc10fb8bec397b399a0fdded53cc9a2d8e30891bb0d63724d25c37bef

TOOLCHAIN=${TOOLCHAIN:-/data/toolchain}
[ $# -eq 0 ] || VERSION=$1
PREFIX="$TOOLCHAIN/rsync/$VERSION"

if [ ! -x "$PREFIX/bin/rsync" ]; then
	export TMPDIR=${TMPDIR:-/data/tmp}
	mkdir -p "$TMPDIR"
	TMP=$(mktemp -d "$TMPDIR/rsync-install-XXXXXX")
	trap 'rm -rf "$TMP"' EXIT
	(
		cd "$TMP"
		curl -fsSL -O "https://download.samba.org/pub/rsync/src/rsync-$VERSION.tar.gz"
		echo "$SHA256  rsync-$VERSION.tar.gz" | sha256sum -c -
		tar xzf "rsync-$VERSION.tar.gz"
		cd "rsync-$VERSION"
		# Every optional feature a distro rsync carries stays on: the
		# conformance suite compares our output byte for byte against this
		# binary, negotiated checksum and compression lists included, so a
		# reference built without xxhash/zstd/lz4 answers differently from the
		# 3.5.1 the suite was written against. Only the manpage generator is
		# off; the tarball ships the manpages.
		./configure --prefix="$PREFIX" --disable-md2man >configure.log 2>&1 ||
			{ tail -20 configure.log >&2; exit 1; }
		make -j"$(nproc)" >build.log 2>&1 || { tail -20 build.log >&2; exit 1; }
		make install >install.log 2>&1
	)
fi

# The shim keeps PATH order honest: /data/toolchain/bin comes before /usr/bin,
# so the conformance suite finds this rsync and not Ubuntu's 3.2.7.
install -d "$TOOLCHAIN/bin"
ln -sfn "../rsync/$VERSION/bin/rsync" "$TOOLCHAIN/bin/rsync"

# pi-builtins' live daemon test ignores PATH: it tries /opt/homebrew/bin/rsync,
# /usr/local/bin/rsync and /usr/bin/rsync in that order
# (crates/pi-builtins/src/rsync/daemon.rs, live_reference_daemon_listing_and_auth),
# so the reference also has to sit in /usr/local/bin, ahead of Ubuntu's 3.2.7.
if [ "$(readlink -f /usr/local/bin/rsync 2>/dev/null)" != "$PREFIX/bin/rsync" ]; then
	if sudo -n true 2>/dev/null; then
		sudo ln -sfn "$PREFIX/bin/rsync" /usr/local/bin/rsync
	else
		echo "run as root: ln -sfn $PREFIX/bin/rsync /usr/local/bin/rsync" >&2
	fi
fi
echo "rsync    $("$TOOLCHAIN/bin/rsync" --version | head -1)  ($TOOLCHAIN/bin/rsync)"
