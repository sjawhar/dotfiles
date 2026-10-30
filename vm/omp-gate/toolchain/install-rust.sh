#!/usr/bin/env bash
# Install the Rust toolchain oh-my-pi pins, inside /data/toolchain, with its own
# RUSTUP_HOME and CARGO_HOME so nothing lands on the root filesystem and no
# lane's ~/.cargo is touched. Idempotent: rerunning it is a no-op plus a
# version print.
#
# Usage: install-rust.sh [channel] [--show]
#   install-rust.sh                      # the pinned channel below
#   install-rust.sh nightly-2026-11-03   # raise the pin (also edit CHANNEL)
#   install-rust.sh --show               # print the pin and exit
#
# Raising the Rust floor is this one command; CHANNEL below is the only place
# the toolchain names a Rust version, the way install-bun.sh works for bun. It
# must equal `channel` in the repo's rust-toolchain.toml — the repo is the
# upstream pin, this is its copy on the VM, and --check-repo compares them.
set -euo pipefail

# --- the pin -----------------------------------------------------------------
# Upstream main pinned nightly-2026-10-06 on 2026-10-29. A revision that pins a
# different channel costs nothing here: rustup reads that worktree's
# rust-toolchain.toml and auto-installs the channel, with its components and
# targets, on first use.
CHANNEL=nightly-2026-10-06
COMPONENTS=(rustfmt clippy rust-analyzer)                                      # rust-toolchain.toml [toolchain] components
TARGETS=(x86_64-unknown-linux-gnu x86_64-pc-windows-msvc aarch64-pc-windows-msvc) # ... targets
NEXTEST_VERSION=0.9.149 # `cargo nextest`, which `bun run test:rs` requires; not pinned by the repo

TOOLCHAIN=${TOOLCHAIN:-/data/toolchain}
export RUSTUP_HOME="$TOOLCHAIN/rust/rustup"
export CARGO_HOME="$TOOLCHAIN/rust/cargo"

if [ "${1:-}" = "--show" ]; then
	echo "$CHANNEL"
	exit 0
fi
if [ "${1:-}" = "--check-repo" ]; then
	# --check-repo <worktree>: fail if the repo pins a different channel.
	repo=${2:?usage: install-rust.sh --check-repo <worktree>}
	want=$(sed -n 's/^channel *= *"\(.*\)"/\1/p' "$repo/rust-toolchain.toml")
	[ "$want" = "$CHANNEL" ] || { echo "pin drift: repo wants $want, $0 installs $CHANNEL" >&2; exit 1; }
	echo "pin matches: $CHANNEL"
	exit 0
fi
[ $# -eq 0 ] || CHANNEL=$1

export TMPDIR=${TMPDIR:-/data/tmp}
mkdir -p "$TMPDIR"
TMP=$(mktemp -d "$TMPDIR/rust-install-XXXXXX")
trap 'rm -rf "$TMP"' EXIT

# --- rustup itself, checksum-verified against static.rust-lang.org -----------
if [ ! -x "$CARGO_HOME/bin/rustup" ]; then
	RUSTUP_BASE=https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu
	(
		cd "$TMP"
		curl -fsSL -O "$RUSTUP_BASE/rustup-init"
		curl -fsSL -o rustup-init.sha256 "$RUSTUP_BASE/rustup-init.sha256"
		# The published file names the path ./rustup-init.
		sha256sum -c rustup-init.sha256
		chmod +x rustup-init
		./rustup-init -y --no-modify-path --default-toolchain none --profile minimal >/dev/null
	)
fi
RUSTUP="$CARGO_HOME/bin/rustup"

# --- the pinned toolchain ----------------------------------------------------
args=(--profile minimal --no-self-update)
for c in "${COMPONENTS[@]}"; do args+=(-c "$c"); done
for t in "${TARGETS[@]}"; do args+=(-t "$t"); done
"$RUSTUP" toolchain install "$CHANNEL" "${args[@]}"
# A default makes cargo usable outside a checkout; inside one, rust-toolchain.toml wins.
"$RUSTUP" default "$CHANNEL" >/dev/null

# The same, for a per-user rustup that predates this toolchain. The PATH work
# below covers a caller that inherits PATH, but not one that runs
# `source ~/.cargo/env` by hand — ~/.profile does it too — which prepends
# $HOME/.cargo/bin and shadows the wrappers with that rustup and its own
# RUSTUP_HOME. Measured on this VM 2026-10-30, with /data/toolchain already
# defaulted and every gate passing:
#   source ~/.cargo/env; cd /tmp && rustc -vV
#     -> "error: rustup could not choose a version of rustc to run, because one
#         wasn't specified explicitly, and no default is configured." (rc 1)
#   source ~/.cargo/env; cd <a checkout> && rustc -vV   -> works
# So it failed only outside a checkout, which is where a harness probing
# `rustc -vV` before it enters one stands, and it reads as a broken harness
# rather than a toolchain nobody selected. Set only when that rustup exists
# and has no default of its own; never overrides a choice someone made.
USER_RUSTUP="$HOME/.cargo/bin/rustup"
if [ -x "$USER_RUSTUP" ] && ! "$USER_RUSTUP" default >/dev/null 2>&1; then
	"$USER_RUSTUP" toolchain install "$CHANNEL" --profile minimal --no-self-update >/dev/null
	"$USER_RUSTUP" default "$CHANNEL" >/dev/null
	echo "per-user rustup at $USER_RUSTUP had no default; set to $CHANNEL"
fi

# --- cargo-nextest, from the release tarball, checksum-verified --------------
NEXTEST_BIN="$CARGO_HOME/bin/cargo-nextest"
if [ "$("$NEXTEST_BIN" nextest --version 2>/dev/null | head -1 | awk '{print $2}')" != "$NEXTEST_VERSION" ]; then
	ASSET=cargo-nextest-$NEXTEST_VERSION-x86_64-unknown-linux-gnu
	BASE=https://github.com/nextest-rs/nextest/releases/download/cargo-nextest-$NEXTEST_VERSION
	(
		cd "$TMP"
		curl -fsSL -O "$BASE/$ASSET.tar.gz"
		curl -fsSL -O "$BASE/$ASSET.sha256"
		# The published .sha256 is already a sha256sum line naming the tarball.
		sha256sum -c "$ASSET.sha256"
		tar xzf "$ASSET.tar.gz" cargo-nextest
		install -m 0755 cargo-nextest "$NEXTEST_BIN"
	)
fi

# --- shims in /data/toolchain/bin -------------------------------------------
# Wrappers, not symlinks: a rustup proxy reached through a symlink would use the
# default RUSTUP_HOME (~/.rustup) and find no default toolchain there.
install -d "$TOOLCHAIN/bin"
for p in cargo rustc rustup rustdoc rustfmt cargo-fmt cargo-clippy clippy-driver rust-analyzer cargo-nextest; do
	# Written aside and renamed into place: a rerun on a live gate VM must not
	# truncate a wrapper that a running lane is in the middle of reading.
	cat >"$TMP/shim" <<EOF
#!/bin/sh
# Generated by $TOOLCHAIN/install-rust.sh — do not edit.
export RUSTUP_HOME="$RUSTUP_HOME"
export CARGO_HOME="$CARGO_HOME"
exec "$CARGO_HOME/bin/$p" "\$@"
EOF
	chmod 0755 "$TMP/shim"
	mv -f "$TMP/shim" "$TOOLCHAIN/bin/$p"
done

# --- reaching the toolchain without an explicit PATH export ------------------
# Measured on this VM 2026-10-29, before this block existed. Two shells, two
# different wrong answers, neither of them this toolchain:
#   bash -lc 'cargo --version'  -> /home/sami/.cargo/bin/cargo, and
#       "error: rustup could not choose a version of cargo to run, because one
#        wasn't specified explicitly, and no default is configured."
#       (a per-user rustup install that predates this toolchain, no default set)
#   bash -c  'cargo --version'  -> "bash: line 1: cargo: command not found"
#       (the default PATH from /etc/environment has no cargo at all)
# A gate therefore worked only for a caller that exported PATH by hand. Two
# fixes, because the two shells build PATH from different places:
#   * /usr/local/bin symlinks — already in the default non-interactive PATH, so
#     they cover `bash -c` and `ssh <vm> <command>`. Same trick, and the same
#     sudo fallback, as install-rsync.sh.
#   * /etc/profile.d — a login shell runs ~/.profile after /etc/profile, and
#     ~/.profile sources ~/.cargo/env, which prepends $HOME/.cargo/bin; a
#     /usr/local/bin link alone loses to it.
# Symlinks are enough here *because they point at the wrappers above*, which set
# RUSTUP_HOME/CARGO_HOME before exec. A symlink straight to a rustup proxy would
# not be: the proxy would read the default $HOME/.rustup and hit exactly the
# "no default is configured" error above. Never shortcut these links to
# $CARGO_HOME/bin.
# Everything outside /data is these links plus one profile.d file; deleting them
# puts the box back as it was.
# Only the real toolchain owns the system paths: a throwaway TOOLCHAIN=... run
# must not repoint /usr/local/bin at itself.
PROFILE_D=/etc/profile.d/10-omp-toolchain.sh
SYSTEM_WIRED="skipped (TOOLCHAIN=$TOOLCHAIN)"
if [ "$TOOLCHAIN" = /data/toolchain ]; then
	SYSTEM_WIRED="/usr/local/bin links + $PROFILE_D"
	EXPOSE=(cargo rustc rustup rustdoc rustfmt cargo-fmt cargo-clippy clippy-driver rust-analyzer cargo-nextest bun bunx omp-toolchain-prepare)
	as_root() { # run a command as root, or print it for someone who can
		if [ "$(id -u)" = 0 ]; then "$@"
		elif sudo -n true 2>/dev/null; then sudo "$@"
		else echo "run as root: $*" >&2; fi
	}
	for p in "${EXPOSE[@]}"; do
		[ -e "$TOOLCHAIN/bin/$p" ] || continue # bun is linked by provision.sh, which runs this script after it
		[ "$(readlink "/usr/local/bin/$p" 2>/dev/null)" = "$TOOLCHAIN/bin/$p" ] && continue
		as_root ln -sfn "$TOOLCHAIN/bin/$p" "/usr/local/bin/$p"
	done

	cat >"$TMP/profile.d.sh" <<EOF
# Generated by $TOOLCHAIN/install-rust.sh — do not edit; rerun that script.
# Login shells: put this toolchain ahead of a per-user rustup install, without
# removing that install from PATH. Seeding \$HOME/.cargo/bin at the end is what
# keeps it behind: ~/.cargo/env (sourced by ~/.profile and ~/.bashrc) prepends
# that directory only when it is absent, so this makes its prepend a no-op.
if [ -d "$TOOLCHAIN/bin" ]; then
	case ":\$PATH:" in
	*:"$TOOLCHAIN/bin":*) ;;
	*) PATH="$TOOLCHAIN/bin:\$PATH" ;;
	esac
	if [ -d "\$HOME/.cargo/bin" ]; then
		case ":\$PATH:" in
		*:"\$HOME/.cargo/bin":*) ;;
		*) PATH="\$PATH:\$HOME/.cargo/bin" ;;
		esac
	fi
	export PATH
fi
EOF
	cmp -s "$TMP/profile.d.sh" "$PROFILE_D" || as_root install -m 0644 "$TMP/profile.d.sh" "$PROFILE_D"
fi

PATH="$TOOLCHAIN/bin:$PATH"
echo "channel  $CHANNEL"
echo "cargo    $(cargo --version)  ($TOOLCHAIN/bin/cargo)"
echo "rustc    $(rustc --version)"
echo "clippy   $(cargo-clippy --version)"
echo "rustfmt  $(rustfmt --version)"
echo "nextest  $(cargo-nextest nextest --version)"
echo "rustup   $RUSTUP_HOME   cargo home $CARGO_HOME"
echo "on PATH  $SYSTEM_WIRED   (default shell finds: $(env -i /bin/bash -c 'command -v cargo'))"
