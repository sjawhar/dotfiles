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

Each is enforced with the tool's own cleaner where the tool has one, and no tool here can trim to
a size, so over the cap the cache is emptied and the next build, resolve or download is a cold
one. Everything removed is re-downloadable or rebuildable; none of it is a working copy. Where a
tool has no non-interactive cleaner at all, the pass removes the cache directory itself.

| Cache | Variable | Default dir | Cap | Over the cap |
|---|---|---|---|---|
| uv | `UV_CACHE_DIR` | `/mnt/nvme/<user>/cache/uv` | **40 GB** | `uv cache prune` (drops entries no env points at); if still over, `uv cache clean` |
| cargo | `CARGO_HOME` | `/mnt/nvme/<user>/cargo` | **20 GB** | remove `registry/cache`, `registry/src`, `git/checkouts`, `git/db` (keeps `registry/index`, `bin/`, config) |
| Go modules | `GOMODCACHE` | `/mnt/nvme/<user>/gomodcache` | **40 GB** | `go clean -modcache` |
| Hugging Face hub | `HF_HOME` | `/mnt/nvme/<user>/cache/huggingface` | **20 GB** | **no cleaner**: `hf cache delete` only asks interactively which revisions to drop, so the pass removes the directory |
| Playwright browsers | `PLAYWRIGHT_BROWSERS_PATH` | `/mnt/nvme/<user>/cache/ms-playwright` | **10 GB** | **no cleaner**: `playwright uninstall` lives in a project's package, not on PATH, and `PLAYWRIGHT_SKIP_BROWSER_GC=1` (`.bashrc`) stops Playwright's own GC, so the pass removes the directory |
| mise | `MISE_CACHE_DIR` | `/mnt/nvme/<user>/cache/mise` | **5 GB** | `mise cache clear` (version lists and release metadata only) |
| Puppeteer browsers | `PUPPETEER_CACHE_DIR` | `/mnt/nvme/<user>/cache/puppeteer` | **10 GB** | **no cleaner**: `@puppeteer/browsers` can only uninstall a named build, so the pass removes the directory |
| pip | `PIP_CACHE_DIR` | `/mnt/nvme/<user>/cache/pip` | **10 GB** | `pip cache purge` |
| bun install cache | `BUN_INSTALL_CACHE_DIR` | `/mnt/nvme/<user>/cache/bun` | **20 GB** | `bun pm cache rm` (install cache and bunx packages; `~/.bun`'s binaries and link store are a different directory and are not touched) |

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

The Go **build** cache — `GOCACHE`, a different and faster-growing directory — has its own,
earlier unit: `devbox/gocache-cap.service`, 80 GB. This unit does not touch it.

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

## Running it by hand

```sh
devbox/nvme-cache-cap --dry-run    # report every cache and what it would do; clean nothing
journalctl --user -u nvme-cache-cap.service -n 50
```

`NVME_CACHE_ROOT` overrides the root, and `UV_CACHE_CAP_BYTES`, `CARGO_CACHE_CAP_BYTES`,
`GOMODCACHE_CAP_BYTES`, `HF_CACHE_CAP_BYTES`, `PLAYWRIGHT_CACHE_CAP_BYTES`, `MISE_CACHE_CAP_BYTES`,
`PUPPETEER_CACHE_CAP_BYTES`, `PIP_CACHE_CAP_BYTES` and `BUN_CACHE_CAP_BYTES` override the caps for
a test.

## Rollback

```sh
systemctl --user disable --now nvme-cache-cap.timer
```

The caches stay where they are and simply stop being capped; nothing moves back. To also move the
caches off the big disk, unset the variables in `scripts/agent-session` (revert its commit) — a
new session then uses the home defaults, and an agent box already does, since it mounts no
`/mnt`.
