# /data/toolchain manifest

Built 2026-10-29 on `omp-infra-20261025` (the gate VM, `ompinfra`), x86_64, AVX2 present
(so the `bun-linux-x64` "modern" build, not `-baseline`).

## Rust nightly-2026-10-06

- The pin: `channel` in the repo's `rust-toolchain.toml` at upstream `main`
  (`cf07b545`, 2026-10-29), with its components (`rustfmt`, `clippy`, `rust-analyzer`) and
  targets (`x86_64-unknown-linux-gnu`, `x86_64-pc-windows-msvc`, `aarch64-pc-windows-msvc`).
  `/data/toolchain/install-rust.sh` carries that version and nothing else does;
  `install-rust.sh --check-repo <worktree>` fails if a worktree pins another channel, and
  `--show` prints the pin.
- `rustc 1.101.0-nightly (ea137335b 2026-10-05)`, `cargo 1.101.0-nightly (f3865b2a4
  2026-09-29)`, `clippy 0.1.101`, `rustfmt 1.11.0-nightly`.
- Installed by `rustup-init` from
  <https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init>, verified
  against that directory's `rustup-init.sha256`
  (`5be191e8c70a5007074254cb74d78dbfdd25cd5a61c27de7b8e5410122ec24f4`) at install time.
- `RUSTUP_HOME=/data/toolchain/rust/rustup`, `CARGO_HOME=/data/toolchain/rust/cargo`, so
  the toolchain, the registry cache and anything `cargo install` writes stay on `/data`
  (3.4 TB) instead of the 247 GB root filesystem.
- `/data/toolchain/bin/{cargo,rustc,rustup,rustdoc,rustfmt,cargo-fmt,cargo-clippy,clippy-driver,rust-analyzer,cargo-nextest}`
  are wrapper scripts, not symlinks: a rustup proxy reached through a symlink would look
  for `~/.rustup` and find no default toolchain there.
- A revision that pins a different channel needs no change here — rustup reads that
  worktree's `rust-toolchain.toml` and auto-installs the channel with its components and
  targets. `nightly-2026-08-12` is installed for that reason.
- Measured 2026-10-29: `rust/rustup` 1.8 GB (both channels, with the Windows `rust-std`
  targets), `rust/cargo` 1.8 GB (the crates.io index and sources after a full workspace
  build), `rsync/3.5.1` 2.5 MB. `/data/toolchain` as a whole: 3.8 GB.

## cargo-nextest 0.9.149

- `bun run test:rs` runs `cargo nextest`; rustup does not ship it.
- Source:
  <https://github.com/nextest-rs/nextest/releases/download/cargo-nextest-0.9.149/cargo-nextest-0.9.149-x86_64-unknown-linux-gnu.tar.gz>,
  verified against the release's own `.sha256` at install time.
- Installed at `/data/toolchain/rust/cargo/bin/cargo-nextest`,
  `cargo-nextest 0.9.149 (3fe4ef319 2026-10-20)`.

## rsync 3.5.1

- Why: `pi-builtins`' rsync tests use a system rsync as a live reference peer. The daemon
  test asserts protocol 33, and the differential conformance suite compares our output byte
  for byte against rsync **3.5.1** and skips itself when no 3.5.1 is present. Ubuntu 24.04
  ships 3.2.7 (protocol 31), which fails two tests outright.
- Source: <https://download.samba.org/pub/rsync/src/rsync-3.5.1.tar.gz>, sha256
  `c55f9c9dc10fb8bec397b399a0fdded53cc9a2d8e30891bb0d63724d25c37bef`, checked at install
  time by `/data/toolchain/install-rsync.sh`, which also carries the version.
- Built on this host with `./configure --prefix=/data/toolchain/rsync/3.5.1
  --disable-md2man` — every optional feature on (xxhash, zstd, lz4, IDN, openssl), because
  the conformance suite compares negotiated checksum and compression lists too. The build
  dependencies are in `setup.sh`'s apt line.
- `rsync version 3.5.1 protocol version 33`, installed at
  `/data/toolchain/rsync/3.5.1/bin/rsync`.
- **Two links point at it**: `/data/toolchain/bin/rsync`, and `/usr/local/bin/rsync`,
  which `install-rsync.sh` creates with `sudo`. The second is unavoidable: the live daemon
  test ignores `PATH` and tries `/opt/homebrew/bin/rsync`, `/usr/local/bin/rsync`,
  `/usr/bin/rsync` in that order. It shadows Ubuntu's `/usr/bin/rsync` 3.2.7 for every user
  on this VM; 3.5.1 still talks to older peers, and the old binary is untouched.

## bun 1.4.4

- Source: <https://github.com/oven-sh/bun/releases/download/bun-v1.4.4/bun-linux-x64.zip>
- Archive sha256: `bf8ccbfb2b0b80159e0f9a6d15c55317add8077534dbc6130a3edb52fccb395c`
  — matches the release's own `SHASUMS256.txt`
  (<https://github.com/oven-sh/bun/releases/download/bun-v1.4.4/SHASUMS256.txt>), verified
  with `sha256sum -c` at install time.
- Installed binary: `/data/toolchain/bun/1.4.4/bin/bun`,
  sha256 `10702245766b225e70bbf6235fd6b47214fd94b4760d00ffaed8ab0df57ac235`,
  `bun --version` → `1.4.4`.
- The same bytes as the hand-made `~/.bun-1.4.4/bin/bun` already on this VM (identical
  sha256); that copy was left in place.
- Rebuild: `/data/toolchain/install-bun.sh 1.4.4` (add `--make-default` to repoint
  `/data/toolchain/bin/bun`).

## pi_natives addon

- `/data/toolchain/natives/pi_natives-553f0d10.node`, 150243112 bytes,
  sha256 `c6d2d0513a13c295a4ddd5b33861ddde8c2a453212068f3e019546525580bd59`.
- `/data/toolchain/natives/pi_natives.node` is a symlink to it: the "current" addon.
- Copied byte for byte from `/home/sami/natives-553f0d10.node` (mtime 2026-10-26 15:41),
  which stays where it is — lanes that name that path still work.
- Provenance: built from `can1357/oh-my-pi` commit
  `553f0d10e813a5e1fb65e545efe7c814bc03a879` ("Merge PR #17087: fix(advisor): wait out
  timed Codex capacity 429s instead of dropping reviews", 2026-10-26).
- Rebuild from a checkout of that commit:
  `cd packages/natives && bun run build` (`scripts/bazel-natives.ts host --dest native`),
  then take `packages/natives/native/pi_natives.linux-x64-modern.node`.
- Load check: `bun -e 'process.dlopen({exports:{}}, "/data/toolchain/natives/pi_natives.node")'`
  returns without error (napi module, loads under bun 1.4.4 on this host).

## Adding a newer addon

Copy it in beside this one as `pi_natives-<commit>.node`, repoint the
`pi_natives.node` symlink, and add its commit and sha256 here. Never overwrite an
existing file: a lane may be running against it.

## What this toolchain put outside `/data`

Everything else lives under `/data/toolchain`. These are the only files it owns anywhere
else, all recreated idempotently by rerunning the script named against each.

| Path | Kind | Written by | Why |
| --- | --- | --- | --- |
| `/usr/local/bin/rsync` | symlink → `/data/toolchain/rsync/3.5.1/bin/rsync` | `install-rsync.sh` | the live daemon test ignores `PATH` and reads this path |
| `/usr/local/bin/{cargo,rustc,rustup,rustdoc,rustfmt,cargo-fmt,cargo-clippy,clippy-driver,rust-analyzer,cargo-nextest,bun,bunx,omp-toolchain-prepare}` | symlinks → `/data/toolchain/bin/<same name>` | `install-rust.sh` | `/usr/local/bin` is the only toolchain directory in the default `/etc/environment` PATH, so this is what makes `bash -c 'cargo --version'` and `ssh <vm> cargo --version` work |
| `/etc/profile.d/10-omp-toolchain.sh` | generated file, 0644 root | `install-rust.sh` | login shells: puts `/data/toolchain/bin` ahead of `$HOME/.cargo/bin`, which `~/.profile` → `~/.cargo/env` would otherwise prepend |

Both `install-rsync.sh` and `install-rust.sh` use `sudo -n` and print the `ln`/`install`
command for a human when they cannot get root.

Before this (measured 2026-10-29, upstream `main`): `bash -lc 'cargo --version'` found
`/home/sami/.cargo/bin/cargo` and printed `error: rustup could not choose a version of
cargo to run … no default is configured`; `bash -c 'cargo --version'` printed
`cargo: command not found`. See README, "Two cargo homes, and which one you get".

To undo: delete the links and the `profile.d` file. Nothing else outside `/data` changed —
`~/.cargo`, `~/.rustup`, `~/.bun*`, `~/natives-*.node` and `/data/wt/*` are untouched.
