# nvme-cache-cap

The shared language caches live on the big disk and are **reused across sessions**; this hourly
timer holds each under a size cap so the reuse never becomes unbounded growth on `/mnt/nvme`.

`scripts/agent-session` points every session's uv, cargo and Go caches at `/mnt/nvme/<user>`
(`.bashrc` sets the same for interactive shells). Shared is the point — a cache is worth moving
off root because every session reuses it — and a cap is what stops a shared cache from filling
the big disk the way the per-session scratch filled root. (dispatch://INFRA-613)

## Why not per-session, and why a cap

Per-session scratch is removed when the session ends (`devbox/session-scratch-reap`). A build
cache is the opposite: its value is that the next session finds it warm, so it is one shared
directory, and the bound is a size cap rather than a lifecycle.

## The three caps

Each is enforced with the tool's own cleaner, and each tool can only empty, not trim to a size,
so over the cap the cache is cleared and the next build or resolve is a cold one. Everything
removed is re-downloadable or rebuildable; none of it is a working copy.

| Cache | Variable | Default dir | Cap | Over the cap |
|---|---|---|---|---|
| uv | `UV_CACHE_DIR` | `/mnt/nvme/<user>/cache/uv` | **40 GB** | `uv cache prune` (drops entries no env points at); if still over, `uv cache clean` |
| cargo | `CARGO_HOME` | `/mnt/nvme/<user>/cargo` | **20 GB** | remove `registry/cache`, `registry/src`, `git/checkouts`, `git/db` (keeps `registry/index`, `bin/`, config) |
| Go modules | `GOMODCACHE` | `/mnt/nvme/<user>/gomodcache` | **40 GB** | `go clean -modcache` |

The caps are generous next to the ~2.2 GB uv and ~7.7 GB Go module caches measured on
sami-rocqperf on 2026-10-27, so a clean happens only when entries no session reuses pile up, and
each is small against the big disk's free-space floor (84 GB, `scripts/disk-fill-watch`).

The Go **build** cache — `GOCACHE`, a different and faster-growing directory — has its own,
earlier unit: `devbox/gocache-cap.service`, 80 GB. This unit does not touch it.

A cache directory outside `/mnt/nvme/<user>` is left alone: the cargo and Go-module passes refuse
a directory that is not under the root, so a caller that set its own cache elsewhere keeps it.

## Proof

```
$ devbox/nvme-cache-cap --dry-run
nvme-cache-cap: uv /mnt/nvme/sami/cache/uv is 2226493385 bytes, cap 40000000000
nvme-cache-cap: gomodcache /mnt/nvme/sami/gomodcache is 7723684178 bytes, cap 40000000000
```

Forced over a 1 MB cap on a scratch cache, the cargo pass removed the download directories and
left `bin/` and `registry/index`:

```
nvme-cache-cap: cargo …/cargo was 5000021 bytes, over the cap of 1000000; removed …/registry/cache …/registry/src; now 21 bytes
```

## Running it by hand

```sh
devbox/nvme-cache-cap --dry-run    # report every cache and what it would do; clean nothing
journalctl --user -u nvme-cache-cap.service -n 50
```

`NVME_CACHE_ROOT`, `UV_CACHE_CAP_BYTES`, `CARGO_CACHE_CAP_BYTES` and `GOMODCACHE_CAP_BYTES`
override the defaults for a test.

## Rollback

```sh
systemctl --user disable --now nvme-cache-cap.timer
```

The caches stay where they are and simply stop being capped; nothing moves back. To also move the
caches off the big disk, unset the variables in `scripts/agent-session` (revert its commit) — a
new session then uses the home defaults, and an agent box already does, since it mounts no
`/mnt`.
