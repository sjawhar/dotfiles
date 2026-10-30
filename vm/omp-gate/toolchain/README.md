# /data/toolchain — prepared toolchain for oh-my-pi verification lanes

**One line for a lane brief:** The prepared toolchain at `/data/toolchain` is already on
PATH, with nothing exported: `bun` 1.4.4, the Rust toolchain the repo pins (`cargo`,
`rustc`, `clippy`, `rustfmt`, `cargo nextest`), the rsync 3.5.1 the Rust tests compare
against, and `omp-toolchain-prepare <worktree>`, which drops the prebuilt `pi_natives`
addon into that worktree. That holds in a login shell, in a plain `bash -c`, and in
`ssh <vm> <command>`. `export PATH=/data/toolchain/bin:$PATH` still works and still says
out loud which toolchain a lane means, so keep it in briefs. Do not install bun, Rust or
the addon yourself.

Host: this VM (`omp-infra-20261025`), reached as `ompinfra`.

## What is here

| Path | What |
| --- | --- |
| `/data/toolchain/bin/bun`, `bunx` | bun **1.4.4** (symlink into `bun/1.4.4/bin/bun`) |
| `/data/toolchain/bun/<version>/bin/bun` | each installed bun, kept side by side |
| `/data/toolchain/bin/cargo`, `rustc`, `rustup`, `rustfmt`, `cargo-clippy`, `cargo-nextest`, … | the pinned Rust toolchain (wrappers, see below) |
| `/data/toolchain/rust/rustup`, `rust/cargo` | that toolchain's `RUSTUP_HOME` and `CARGO_HOME`, off the root filesystem |
| `/data/toolchain/bin/rsync` | rsync **3.5.1**, the reference peer `pi-builtins`' rsync tests need |
| `/data/toolchain/natives/pi_natives.node` | current prebuilt `pi_natives` addon (symlink) |
| `/data/toolchain/bin/omp-toolchain-prepare` | copies that addon into a worktree |
| `/data/toolchain/install-bun.sh` | adds or rebuilds a bun version, checksum-verified |
| `/data/toolchain/install-rust.sh` | installs the pinned Rust toolchain and `cargo-nextest`, and wires the toolchain onto the default PATH (idempotent) |
| `/data/toolchain/install-rsync.sh` | builds and installs rsync 3.5.1 (idempotent) |
| `/data/toolchain/provision.sh` | rebuilds this whole directory on a fresh gate VM (idempotent) |
| `/data/toolchain/MANIFEST.md` | versions, checksums, where every file came from |
| `/usr/local/bin/*` → `/data/toolchain/bin/*` | the only reason `cargo` works in `bash -c` and `ssh <vm> <command>`; see [What this does not touch](#what-this-does-not-touch) |
| `/etc/profile.d/10-omp-toolchain.sh` | the same for login shells, where `$HOME/.cargo/bin` would otherwise win |

## Using it in a lane

```bash
export PATH=/data/toolchain/bin:$PATH
export BUN_INSTALL_CACHE_DIR=/data/bun-cache TMPDIR=/data/tmp
bun --version                                   # 1.4.4
/data/toolchain/bin/omp-toolchain-prepare /data/wt/<lane>
cd /data/wt/<lane> && bun install --frozen-lockfile
```

Run `omp-toolchain-prepare` in **both** arms of an A/B gate (candidate worktree and the
upstream-`main` baseline). It keeps an addon file that is already in the worktree; pass
`--force` to overwrite one.

**A branch that changes anything under `crates/` needs its own build and must not use the
prepared addon.** `/data/toolchain/natives/pi_natives.node` is pinned to upstream
`553f0d10`; gating a Rust change against it tests a stale addon. Build that branch's addon
(`cd packages/natives && bun run build`) and leave it in place — `omp-toolchain-prepare`
without `--force` keeps an addon the worktree already has.

## Running the Rust half

Cargo is on PATH with or without the export above, so the whole gate runs, both halves:

```bash
cd /data/wt/<lane>
bun check                       # check:ts + check:rs
CI=1 bun run check:rs           # cargo fmt --check, then clippy -D warnings
CI=1 bun run test:rs            # cargo nextest over the workspace, then the doctest pass
cargo nextest run -p pi-vcs     # one crate
```

`check:rs` and `test:rs` skip themselves outside CI when the branch changes no
Rust-affecting file, so force them with `CI=1` when you want the Rust half to run.

What a Rust gate costs on this VM (64 cores, measured 2026-10-29 on upstream `main`
`cf07b545`, with the crates.io cache already in `/data/toolchain/rust/cargo`):

| Step | Wall clock | Scale |
| --- | --- | --- |
| `cargo metadata --locked` | 15 s the first time (fetches the crates.io index), under 1 s after | 1064 packages, 18 workspace members |
| `bun check`, no Rust change (`check:rs` self-skips) | 31 s | the TypeScript half alone |
| `CI=1 bun run check:rs` | 55 s from an empty target dir, 45 s warm | clippy over the workspace |
| `CI=1 bun run test:rs` | 79 s after that check, 38 s warm | 4445 tests, 19–37 s inside nextest |
| `cargo nextest run -p pi-vcs` | 36 s, that crate from cold | 98 tests |

So a branch that touches `crates/` pays **about 2¼ minutes** for its first full Rust gate
(check:rs then test:rs from an empty target dir) and seconds after that, and the target dir
costs **5.4 GB** (1.1 GB after clippy, the rest is the test binaries). Give each lane its
own `CARGO_TARGET_DIR` under `/data` — cargo judges a path crate by mtime and does not hash
the worktree path, so a shared target dir hands one worktree's crates to another.

The same holds for the native addon. Building it from a worktree
(`OMP_NATIVE_BUILD_BACKEND=cargo bun run build` in `packages/natives`) writes into the
worktree's own `target/` unless you say otherwise, so two lanes building addons on this box
either share one directory and serialise on its lock, or rebuild each other's path crates.
Give each arm an explicit directory outside the worktree — `/data/targets/<lane>`, about
5.4 GB per arm — and delete it when the lane ends. Budget the full first build (the ~2¼
minutes above) plus the addon link; the 2 m 10 s a lane measured for the `local` profile
was with that target dir already carrying a `cargo check`'s dependency graph, so it is not
a floor for an empty one.

### Reds on upstream `main` that are not yours

Measured on `cf07b545` and re-checked on `31147f9d1a`, 2026-10-29. Check against these
before you blame a branch:

- `check:rs` **was** failing with 31 clippy errors in `crates/vendor/grep-searcher`, a crate
  vendored the same day (`7d96ec3722`) that nobody had added to the lint excludes in
  `scripts/run-rs-task.ts`. Upstream fixed it: `31147f9d1a` carries
  `LINT_EXEMPT_FORK_EXCLUDES`, which adds `--exclude grep-searcher` on top of
  `VENDORED_FORK_EXCLUDES`. `CI=1 bun run check:rs` on `31147f9d1a` exits 0 (measured
  2026-10-29 from an empty target dir; clippy reported `Finished dev profile … in 53.84s`).
- `test:rs` fails the three `rsync::conformance::*_transfers_match_reference` tests
  (`daemon`, `local`, `remote_shell`). Two causes, both upstream, neither caused by a
  branch: the suite runs the reference with `env_clear()`, so rsync sees the C locale and
  escapes non-ASCII file names while the builtin does not — giving the reference
  `LC_ALL=C.UTF-8` turns `daemon` and `remote_shell` green, and
  `fix/rsync-conformance-reference-locale` owns that half. What then remains is `local`
  alone, 3 of its 148 scenarios, genuine Linux behaviour differences (a `chmod` on a
  directory symlink reports `ENOTSUP` from ours and `ELOOP` from rsync, plus one `-rv`
  ordering difference). The suite only runs where rsync 3.5.1 exists, which until now was
  nowhere, so all of it is newly visible, not newly broken. Independently confirmed on two
  arms differing only in `crates/vendor/brush-core/src/interp.rs`: over
  `-p brush-core -p pi-shell -p pi-builtins`, 3041 tests with 3038 passed and the same 3
  failed on both arms. That count is the **pre-fix** baseline, measured 2026-10-29 before
  `fix/rsync-conformance-reference-locale` landed. Once it lands, expect **3040 passed, 1
  failed** (`local` alone) over those three crates; a lane comparing arms against the older
  numbers would read the locale fix as a regression.

`bun --version` printing anything other than 1.4.4 means `PATH` did not take: the VM's
default `~/.bun/bin/bun` is 1.4.2, and upstream `main` needs ≥ 1.4.4. A gate round run on
1.4.2 fails about 100 CLI tests on both arms and is worthless — check the version before
you trust a result.

## Two cargo homes, and which one you get

This VM has two Rust installs. Both are real; only one is the gate's.

| | The gate's | The one that was here first |
| --- | --- | --- |
| `cargo`, `rustc`, `cargo-nextest` on PATH | `/data/toolchain/bin` (and the `/usr/local/bin` links to it) | `$HOME/.cargo/bin` |
| `RUSTUP_HOME`, `CARGO_HOME` | `/data/toolchain/rust/rustup`, `/data/toolchain/rust/cargo` | `~/.rustup`, `~/.cargo` — 4.1 GB |
| Default toolchain | `nightly-2026-10-06`, set by `install-rust.sh` | none; `rustup default` was never run there |
| Installed by | `install-rust.sh`, declared in `MANIFEST.md` | nobody wrote it down |

Tell them apart in one command:

```bash
rustup which cargo
# /data/toolchain/rust/rustup/toolchains/nightly-2026-10-06-…/bin/cargo  -> the gate's
# /home/sami/.rustup/toolchains/…                                       -> the older one
```

The older install stays, and is deliberately left alone: the gate scripts in `~`
(`prep-base.sh`, `gate.sh`, the `*-gate.sh` fleet) name `$HOME/.cargo/bin` in their own
`PATH` exports, and 4.1 GB against 3.2 TB free buys nothing. Nothing here changes it. It
still answers, outside a checkout:

```
error: rustup could not choose a version of cargo to run, because one wasn't specified
explicitly, and no default is configured.
```

Inside a checkout it works, because rustup reads that worktree's `rust-toolchain.toml` and
that home happens to have `nightly-2026-10-06` installed too — which is why those scripts
never noticed. What it does **not** have is a usable `cargo` for anything outside a
checkout, so leaving it ahead of this toolchain on PATH was the bug fixed on 2026-10-29.

**One leak worth knowing.** `scripts/run-rs-task.ts` asks `rustup which cargo` and then runs
that absolute path, so the `CARGO_HOME` the wrapper exported does not reach it: that cargo
falls back to `~/.cargo` and takes its **subcommands** from `~/.cargo/bin`. Measured
2026-10-29:

```
$(rustup which cargo) nextest …  ->  execs /home/sami/.cargo/bin/cargo-nextest
cargo nextest …                  ->  execs /data/toolchain/rust/cargo/bin/cargo-nextest
```

It costs nothing today: both `cargo-nextest` binaries are byte-identical (sha256
`7f557542ea0d9d7218c2d564987a2cebf367de1d0f9eca45f93f270932a3d9ff`, 0.9.149) and the
compiler is the pinned one either way. A lane that wants the registry and the subcommands
on `/data` as well exports them explicitly:

```bash
export CARGO_HOME=/data/toolchain/rust/cargo RUSTUP_HOME=/data/toolchain/rust/rustup
```

## Raising the bun floor

One command, nothing else names a version:

```bash
/data/toolchain/install-bun.sh 1.4.5 --make-default
```

Then record the new version and checksum in `MANIFEST.md`.

## Raising the Rust pin

The pin lives in `install-rust.sh` and nowhere else, the way the bun version lives in
`install-bun.sh`:

```bash
/data/toolchain/install-rust.sh --show                       # what is pinned here
/data/toolchain/install-rust.sh --check-repo /data/wt/<lane> # does the repo agree?
```

When upstream moves `rust-toolchain.toml`, edit `CHANNEL` in `install-rust.sh` and rerun
it; it is idempotent (a no-op rerun takes 0.3 s). Nothing breaks in the meantime: rustup
reads a worktree's own `rust-toolchain.toml` and auto-installs whatever channel it names,
with that file's components and targets. Record the new channel in `MANIFEST.md`.

## What this does not touch

`~/natives-553f0d10.node` and the other `~/natives-*.node` files stay where they are, and
`~/.bun`, `~/.bun-1.4.4` and `/data/wt/*` are untouched. Lanes that already name those
paths keep working. The same holds for Rust: `~/.cargo` and `~/.rustup` (the hand-made
install `gate.sh` and `prep-base.sh` use through `$HOME/.cargo/bin`) are left exactly as
they were — this toolchain is a second, declared install under `/data/toolchain/rust`.

Outside `/data` there are exactly two kinds of change, and deleting them puts the box back
as it was:

- **`/usr/local/bin` symlinks** into `/data/toolchain/bin`: `cargo`, `rustc`, `rustup`,
  `rustdoc`, `rustfmt`, `cargo-fmt`, `cargo-clippy`, `clippy-driver`, `rust-analyzer`,
  `cargo-nextest`, `bun`, `bunx` and `omp-toolchain-prepare`, written by `install-rust.sh`,
  plus `rsync` written by `install-rsync.sh`. `/usr/local/bin` is in the default PATH that
  `/etc/environment` hands every session, which is the only PATH an agent's `bash -c` tool
  call and `ssh <vm> <command>` ever see; before these links, `bash -c 'cargo --version'`
  answered `cargo: command not found`. `rsync` needed its link for a second reason:
  `pi-builtins`' live daemon test ignores `PATH` and reads `/usr/local/bin/rsync` directly
  (`crates/pi-builtins/src/rsync/daemon.rs`), and the link also shadows Ubuntu's
  `/usr/bin/rsync` 3.2.7.
- **`/etc/profile.d/10-omp-toolchain.sh`**, written by `install-rust.sh`, for login shells
  only. A login shell runs `~/.profile` after `/etc/profile`, and `~/.profile` sources
  `~/.cargo/env`, which prepends `$HOME/.cargo/bin` — so a `/usr/local/bin` link alone loses
  there, and `bash -lc 'cargo --version'` used to get the older install's "no default is
  configured". The file prepends `/data/toolchain/bin` and seeds `$HOME/.cargo/bin` at the
  **end** of `PATH`; `~/.cargo/env` only prepends that directory when it is absent, so the
  seeding makes its prepend a no-op and keeps the older install reachable, behind this one.

Both are symlinks or a generated file, both are recreated idempotently by rerunning
`install-rust.sh`, and the links point at the **wrappers** in `/data/toolchain/bin`, never
at a rustup proxy. That part is not cosmetic: a proxy reached through a symlink reads the
default `~/.rustup`, finds no default toolchain, and reproduces the exact error this fix
removed. See `MANIFEST.md`.
