# nvme-cache-cap

The shared language caches live on the big disk and are **reused across sessions**; this hourly
timer holds each under a size cap so the reuse never becomes unbounded growth on `/mnt/nvme`.

`scripts/agent-session` points every session's uv, cargo, Go, Hugging Face, Playwright, mise,
Puppeteer, pip and bun caches at `/mnt/nvme/<user>`
(`.bashrc` sets the same for interactive shells). Shared is the point — a cache is worth moving
off root because every session reuses it — and a cap is what stops a shared cache from filling
the big disk the way the per-session scratch filled root. (dispatch://INFRA-613)

## Why not per-session, and why a cap

Per-session scratch is removed when the session ends (`devbox/session-scratch-reap`). A build
cache is the opposite: its value is that the next session finds it warm, so it is one shared
directory, and the bound is a size cap rather than a lifecycle.

## The caps

No tool here can trim to a size, so over the cap the cache is removed whole and the next build,
resolve or download is a cold one. Everything removed is re-downloadable or rebuildable; none of it
is a working copy.

### Never under a running reader

A cache over its cap is removed only when both of these hold, and either one merely unknown keeps
it (dispatch://INFRA-3905):

- No process holds it: no open file, cwd, exe, root, mapped file or command-line argument at or
  under it. `devbox/proc-holds.sh` reads that from `/proc` for every path at once, and
  `devbox/tmp-stale-reap` uses the same code. A scan that does not finish in
  `NVME_CACHE_SCAN_SECONDS` (600) keeps the cache. The Go module cache holds the toolchain the
  builds on sami-devbox run from (`golang.org/toolchain@…/pkg/tool/…/compile`, `link`), so while a
  build runs its compile and link processes hold the cache through their exe.
- None of the commands that read the cache without keeping a file open runs: `go` and `gopls` for
  the Go modules, `cargo` and `rustc` for cargo's sources, `uv`, `pip` and `pip3`, `mise`. bun is
  not one: 168 bun processes ran on sami-devbox on 2026-11-16, nearly all running scripts that never
  open the install cache, and `bun install` holds the files it links.

Then the cache is renamed to a sibling, `<dir>.removing.<time>.<pid>`, so a reader sees the whole
cache or none of it and the tool starts a new one, and the sibling is removed once no process holds
it. A sibling still held, or left by a run that stopped, is removed by a later run. uv's
`uv cache prune` still runs first when nothing holds the uv cache; uv refuses on its own to prune
what a uv process is using.

Until 2026-11-16 the pass ran the cleaners in the last column of the old table (`go clean
-modcache`, `uv cache clean`, `mise cache clear`, `pip cache purge`, `bun pm cache rm`, or `rm -rf`)
with no such check, so a cache could go from under a build reading it. None had reached its cap on
sami-devbox: at 04:00Z the Go modules held 7.8 of 40 GB and uv 15.3 of 40 GB.

A held cache stays over its cap until it is free; the disk floors (`devbox/box-cleanup`,
`scripts/disk-fill-watch`) are what speak if the disk fills.

On sami-devbox the uv and pip caches are on the local-SSD scratch, `/mnt/scratch/<user>/cache/uv`
and `.../cache/pip` (`devbox/gce-disks` makes the directory at every boot; `.bashrc` and
`scripts/agent-session` prefer it when it exists), and the pass caps them there with the same caps.
A stop of the VM empties them too.

| Cache | Variable | Default dir | Cap | Removed over the cap | Commands that read it without holding a file |
|---|---|---|---|---|---|
| uv | `UV_CACHE_DIR` | `/mnt/nvme/<user>/cache/uv` | **40 GB** | `uv cache prune` first (drops entries no env points at); if still over, the directory | `uv` |
| cargo | `CARGO_HOME` | `/mnt/nvme/<user>/cargo` | **20 GB** | `registry/cache`, `registry/src`, `git/checkouts`, `git/db`, each on its own (keeps `registry/index`, `bin/`, config) | `cargo`, `rustc` |
| Go modules | `GOMODCACHE` | `/mnt/nvme/<user>/gomodcache` | **40 GB** | the directory, read-only files included | `go`, `gopls` |
| Hugging Face hub | `HF_HOME` | `/mnt/nvme/<user>/cache/huggingface` | **20 GB** | the directory | — |
| Playwright browsers | `PLAYWRIGHT_BROWSERS_PATH` | `/mnt/nvme/<user>/cache/ms-playwright` | **10 GB** | the directory (`PLAYWRIGHT_SKIP_BROWSER_GC=1` in `.bashrc` stops Playwright's own GC) | — |
| mise | `MISE_CACHE_DIR` | `/mnt/nvme/<user>/cache/mise` | **5 GB** | the directory (version lists and release metadata only) | `mise` |
| Puppeteer browsers | `PUPPETEER_CACHE_DIR` | `/mnt/nvme/<user>/cache/puppeteer` | **10 GB** | the directory | — |
| pip | `PIP_CACHE_DIR` | `/mnt/nvme/<user>/cache/pip` | **10 GB** | the directory | `pip`, `pip3` |
| bun install cache | `BUN_INSTALL_CACHE_DIR` | `/mnt/nvme/<user>/cache/bun` | **20 GB** | the directory (install cache and bunx packages; `~/.bun`'s binaries and link store are a different directory and are not touched) | — |

The caps are generous next to what each cache held on the root disk on 2026-10-30 — Hugging Face
4.5 GB, Playwright 2.2 GB, mise 1.0 GB, Puppeteer 0.7 GB, pip 0.5 GB, bun's install cache 11.1 GB
— so a clean happens only when entries no session reuses pile up, and each is small against the
big disk's free-space floor (84 GB, `scripts/disk-fill-watch`).

### The variables are for new processes only

Setting these moves nothing. A process that is already running keeps the cache directory it
started with, and the directories that already exist under `~/.cache` and `~/.bun` stay exactly
where they are, with their bytes still on the root disk. Only a session started after this change
writes to the big disk. Reclaiming the old directories is a separate, deliberate act — none of
this deletes them.

### How each variable was proved

Every variable here was driven in a throwaway shell before it was landed: the variable was pointed
at a scratch directory under `/mnt/nvme/<user>/tmp`, the tool was run until it wrote its cache, and
both the scratch directory and the matching `~/.cache` directory were measured. On 2026-10-30 on
sami-rocqperf every one of the six put its bytes in the scratch directory and left `~/.cache`
unchanged: `pip download --no-deps six` (104 KB), `mise ls-remote node` (20 KB),
`bun add is-odd` (76 KB), `hf_hub_download('hf-internal-testing/tiny-random-gpt2', 'config.json')`
(56 KB), `playwright install ffmpeg` (5.1 MB) and `puppeteer browsers install chrome-headless-shell`
(676 MB). The three cleaners were driven the same way and each honoured its variable, removing the
scratch cache and leaving the one under `~/.cache` alone.

Note which command proves Puppeteer: `@puppeteer/browsers install` (the standalone CLI) ignores
`PUPPETEER_CACHE_DIR` and installs into the working directory. `puppeteer browsers install`, the
CLI in the `puppeteer` package that every consumer uses, reads it.

The Go **build** caches — `GOCACHE`, a lane's own, and Go's default `~/.cache/go-build`, different
and faster-growing directories — have their own, earlier unit: `devbox/gocache-cap.service`, which
deletes entries unused for 3 h past 25 GB in all and for 2 h past 50 GB, never one a running build
may hold. This unit does not touch them.

A cache directory outside `/mnt/nvme/<user>` is left alone: every pass refuses a directory that is
not under the root, so a caller that set its own cache elsewhere keeps it.

## Proof

On sami-rocqperf, 2026-10-30 16:15:59Z, with the six new caches landed:

```
$ devbox/nvme-cache-cap --dry-run
nvme-cache-cap: uv /mnt/nvme/sami/cache/uv is 14695768724 bytes, cap 40000000000
nvme-cache-cap: cargo /mnt/nvme/sami/cargo is 2854489351 bytes, cap 20000000000
nvme-cache-cap: gomodcache /mnt/nvme/sami/gomodcache is 10202220843 bytes, cap 40000000000
nvme-cache-cap: huggingface /mnt/nvme/sami/cache/huggingface is 0 bytes, cap 20000000000
nvme-cache-cap: ms-playwright /mnt/nvme/sami/cache/ms-playwright is 0 bytes, cap 10000000000
nvme-cache-cap: mise /mnt/nvme/sami/cache/mise is 446 bytes, cap 5000000000
nvme-cache-cap: puppeteer /mnt/nvme/sami/cache/puppeteer is 0 bytes, cap 10000000000
nvme-cache-cap: pip /mnt/nvme/sami/cache/pip is 0 bytes, cap 10000000000
nvme-cache-cap: bun /mnt/nvme/sami/cache/bun is 1391171 bytes, cap 20000000000
```

Forced over a 1 MB cap on a scratch cache, the cargo pass removed the download directories and
left `bin/` and `registry/index`:

```
nvme-cache-cap: cargo …/cargo was 5000021 bytes, over the cap of 1000000; removed …/registry/cache …/registry/src; now 21 bytes
```

Both new branches were forced the same way, against `NVME_CACHE_ROOT=/mnt/nvme/sami/tmp/capscratch`
on 2026-10-30:

```
$ PUPPETEER_CACHE_CAP_BYTES=1000000 devbox/nvme-cache-cap
nvme-cache-cap: puppeteer …/cache/puppeteer was 3000000 bytes, over the cap of 1000000, and has no cleaner of its own; removed the directory
$ PIP_CACHE_CAP_BYTES=1000 devbox/nvme-cache-cap
nvme-cache-cap: pip …/cache/pip was 23679 bytes, over the cap of 1000; ran pip cache purge; now 0 bytes
```

An entry that vanishes while `du` walks a cache (uv removes its own `.tmp*` entries under
`builds-v0`) is not an error: the run takes du's total of what it read. Until 2026-11-02 such an
entry made du exit 1 and ended the whole run, so no cap was checked from 20:39Z until the fix
(`scripts/tests/test_cache_cap_du.py`). Any other du error still fails the run. `gocache-cap`
sizes its cache the same way.

The outputs above are from before 2026-11-16, when the pass still ran each tool's cleaner. The
reader rule's proof is `scripts/tests/test_nvme_cache_cap_readers.py`: a real process holds one
file of a cache open while the pass runs with that cache's cap at 0, then opens a second file of it
by its path, as a build opens its next source. On sami-devbox on 2026-11-16 the pass from before
the change, run the same way against a scratch tree, removed the second file under all nine
readers (uv by `uv cache prune`, the Go modules by `go clean -modcache`, mise by `mise cache
clear`, pip by `pip cache purge` with the files in pip's own `http-v2`, bun by `bun pm cache rm`,
cargo, Hugging Face, Playwright and Puppeteer by `rm -rf`). This pass kept all nine, named each
reader's pid and file, and every reader read its second file. The same caches with no reader were
renamed away and removed, so a kept cache is the reader's doing.

## Running it by hand

```sh
devbox/nvme-cache-cap --dry-run    # every cache's size; for one over its cap, who holds it or what would go
journalctl --user -u box-cleanup.service -n 50
```

`NVME_CACHE_ROOT` and `SCRATCH_CACHE_ROOT` override the roots, `NVME_CACHE_PROC` a scratch `/proc`,
`NVME_CACHE_SCAN_SECONDS` the limit of one scan of it, and `UV_CACHE_CAP_BYTES`,
`CARGO_CACHE_CAP_BYTES`, `GOMODCACHE_CAP_BYTES`, `HF_CACHE_CAP_BYTES`, `PLAYWRIGHT_CACHE_CAP_BYTES`,
`MISE_CACHE_CAP_BYTES`, `PUPPETEER_CACHE_CAP_BYTES`, `PIP_CACHE_CAP_BYTES` and `BUN_CACHE_CAP_BYTES`
override the caps for a test.

## Rollback

```sh
systemctl --user disable --now nvme-cache-cap.timer
```

The caches stay where they are and simply stop being capped; nothing moves back. To also move the
caches off the big disk, unset the variables in `scripts/agent-session` (revert its commit) — a
new session then uses the home defaults, and an agent box already does, since it mounts no
`/mnt`.
