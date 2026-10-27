# omp-box-memory-guard: open defects, bake HALTED 2026-10-27 20:54Z

The dry-run bake started 16:51:40Z and was halted at 20:54Z by the INFRA lead
(session 01a13c57) after a second false shed. **Do not arm the killer** — do not
remove `--dry-run` from `ExecStart` — until defects 1-4 below are fixed and a
fresh bake runs clean for 48-72 h with at least 2-3 real episodes and 1 restart.

The standing rule this enforces: anything that can kill ships report-only first,
and any "would shed" while the box is healthy halts the bake.

## The false shed, 2026-10-27 19:49:48Z

```
WARN: user.slice/user-1000.slice has reached its own memory.max (231.54 GiB);
      128.01 GiB unreclaimable, 103.53 GiB of headroom; told 4 session(s)
DRY RUN: would shed pid:33468 (18.38 GiB); told 2
Box: MemAvailable 135.19 GiB of 251.67 GiB (53.7%); shed level 31.46 GiB
Slice: 215.94 GiB held, events.local high=119118 max=228 oom_kill=0,
       own pressure some 0.1/0.0%, full 0.0/0.0%
19:49:51Z incident cleared: 136.40 GiB available   <- resolved itself in 3 s
```

The target, pid 33468, was a live session (`omp --resume
01a14126-ae14-7525-98b7-6b1ac6cefd80`) 6 h 49 min old. Out of dry run it dies on
a box with 135 GiB free.

## 1. The slice trigger infers starvation from a cap touch (high)

Hitting `memory.max` is treated as evidence a session must die. It is not: the
slice held **103.53 GiB of reclaimable headroom**, and the kernel's answer to a
cap held by page cache is reclaim, which costs nothing. `full` pressure was
**0.0/0.0%** — a slice genuinely starved shows full pressure — and the condition
cleared by itself three seconds later with no action.

This is not the earlier `memory.events` aggregate bug returning. Reading
`memory.events.local` works; `max=228` is genuine. The number is right and the
conclusion drawn from it is wrong.

A slice-level shed must require all three:
- `full` pressure sustained above a threshold on **avg60**, not avg10;
- unreclaimable headroom genuinely small (single-digit GiB), not 103 GiB;
- the box's own MemAvailable near its shed level, so a healthy box can never
  shed on a slice signal alone.

Any one of these would have suppressed this shed.

## 2. Victim size is wrong by about 17x (high)

The guard sized pid 33468 at **18.38 GiB**. Its actual `VmRSS` is **1.06 GiB**.
Killing it frees about a gigabyte, not eighteen.

## 3. Per-pid attribution is impossible in this cgroup layout (high)

pid 33468's cgroup is `/user.slice/user-1000.slice/session-3.scope`, which holds
**1,318 processes (302 of them omp) and 112.61 GiB** between them. No per-cgroup
figure can be attributed to one pid here, so any sizing read from the cgroup is
meaningless for a single-process kill. Size a candidate from the process itself
(`/proc/<pid>/status` `VmRSS`, better `anon` from `smaps_rollup`), never from a
shared scope's `memory.current`.

## 4. Candidates are ranked by reclaimable memory (high)

That scope's `memory.stat` reads `anon 57.99 GiB`, `file 42.07 GiB`, `slab 11.70
GiB`. Ranking by total memory picks whichever session holds the most **file
cache** — a cheap kill that frees nothing the kernel could not reclaim itself —
and spares the session actually holding anon pages. Rank by anon/unreclaimable
memory, and print **both** numbers in the `would shed` line so a reviewer can see
the gap without reconstructing it.

## What stays live meanwhile

The parts that actually protect the box are untouched and need no bake: 64 GiB
swap on /mnt/nvme, `vm.swappiness=10`, `watermark_scale_factor=100`,
`min_free_kbytes=1 GiB`, the `memory.max` ceiling on user-1000.slice, earlyoom
re-armed with `-s 100,100`, and `omp-memory-watch.timer` report-only.
