# farm-fail-watch

Says what killed a build-farm job and what its lane should do about it. It reads; it submits
nothing, resizes nothing and changes no job.

```bash
farm-fail-watch farm/20261104t210028z-2d717d          # print the message
farm-fail-watch farm/20261104t210028z-2d717d --json   # the evidence and the verdict too
farm-fail-watch farm/20261104t210028z-2d717d --send   # Envoy it to the job's owning session
```

## Why the rule is what it is

**Kubernetes reports an ephemeral-storage eviction as `OOMKilled` with exit 137**, exactly as
it reports a memory kill. A watch that reads the exit code and the memory limit therefore
calls every disk kill a memory kill, and the advice that follows makes the lane buy memory
it does not need and be killed again at the same disk size.

That happened on 2026-11-04 at 21:09Z (dispatch://OSSV-175). Job
`farm/20261104t210028z-2d717d`, unit `spirv-tools/spvtools_binary_parser_fuzzer`, kind
oft-check, pod `ossv-farm-20261104t210028z-2d717d-kx45t-qw6vn`, killed 21:05:53Z. The watch
said OOMKilled at a memory limit of 8503Mi and advised at least 1.25 x that. The pod's own
numbers:

| | peak | its own bound | share |
|---|---|---|---|
| memory | 1.89 GiB (`memory_peak_bytes` 2025984000) | 8503Mi limit | 23% |
| disk | 19.66 GiB (`disk_peak_bytes` 21106288640) | 4Gi ephemeral-storage request | 491% |

and the kernel's `oom_kill` counter was **0**. The kill was disk.

## What it does

1. **Collect the evidence**, each part optional and each missing part named in the message:
   - the pod (`kubectl -n <ns> get pod <pod> -o json`): the job container's terminated
     reason and exit code, and that container's own memory limit and ephemeral-storage
     request. A killed pod is often already gone.
   - `result.json` (else `usage.json`) under `<bucket>/out/ossv/<run>/`: the memory and disk
     peaks, `oom_kill`, and the failed pods. This is the only place the peaks survive the pod.
   - `job.json` under `<bucket>/in/ossv/<run>/`: unit, kind, the sizes asked for, `size.mode`
     and the recent peaks the index sized from.

   The bucket is the one the farm recorded for the run in `<ossv-n6>/job-buckets/<run>`, else
   `$OSSV_RUNS_BUCKET`, else `gs://theorem-ossv-runs`.

2. **Name the resource that was at its bound, never the exit code.** Memory peak against the
   memory limit, disk peak against the ephemeral-storage request (eviction is judged against
   the request, not a larger limit). A peak at or above 95% of its own bound is at it; 80% is
   near it. The kernel's `oom_kill` counter above 0 names memory on its own, since nothing
   but the memcg OOM killer raises it.

   | reading | what it says |
   |---|---|
   | one resource near its bound | that one, with both numbers |
   | both near theirs | both, and which was closer |
   | neither, or a peak or bound missing | `NOT DISTINGUISHABLE: killed at exit 137; memory peak X of Y, disk peak Z of W; the kill reason is not distinguishable from these` |

3. **Advise on the named resource only.**
   - memory: serialise when the largest single process held under half the limit (a bigger
     pod buys nothing then); otherwise `--memory` at 1.3 x the measured peak.

     When the log carries **no per-process samples at all** — a job that dies between two
     samples leaves none — `cpu_last_cores` at the last sample stands in, against the job's
     core count (CURL, 2026-11-04 21:25Z): at or above 70% of it, many processes were
     running when the total reached the limit, the parallel case, whose answer is fewer at
     once at the same limit; at or below 1.5 cores, one process was alone, the single
     oversized step, and only then is a bigger pod right; between the two, nothing is
     settled and the message says so. The wording keeps the weaker evidence visible: "no
     per-process samples in this job's log; from cpu_last_cores N of M cores at the last
     sample, this looks like the parallel case", never a flat assertion. Measured:
     `farm/20261104t204327z-bda302` (`curl/fuzz_bufq__build`, killed at its 56Gi limit at
     21:22:12Z) read `cpu_last_cores` 29.336 of 31.0 cores 18 s before the kill with a
     56.80 GiB peak — twenty-nine processes of about 2 GiB each — and its own log was ten
     lines with no per-process memory in it.

     **Every "raise memory" assumes the peak is BOUNDED**, and one reading says when it is
     not: a recent run of the same unit and kind that peaked ABOVE the limit THIS run was
     given. Then the peak is not what the job needs, it is what it was offered, and sizing
     at 1.3 x it buys minutes. The message says so and tells the lane to measure whether the
     growth is bounded before buying memory. The peaks come from `job.json`'s
     `size.recent_peaks`, which the watch already read for the disk warning. Why (CURL,
     2026-11-05): one lane had three memory kills in one morning with three different right
     answers — cap the concurrency of the big files, remove a flag, and fix a file — and the
     watch advised 1.25-1.3 x for all three. The third was a runaway its own base commit
     documents, "two kills at about 101 GB at 8 and at 2 cores… the growth survives at about
     4 GB a minute", so a 35 GiB limit buys about two minutes. The same lane had already
     removed an automatic doubled retry from its tooling (#1780) because growing a runaway's
     limit buys a bigger kill. The serialise answer is never overridden by this: too many
     processes at once is still too many, whatever an earlier run peaked at.

     With neither test available, the message says the serialise test could not be applied
     rather than skipping it silently.
   - disk: `--disk` at 1.3 x the measured disk peak, and "do not raise memory: it was not
     the bound".
   - both: both sizes, neither forbidden.
   - not distinguishable: no raise at all — resubmit once with the sampler's record kept.

4. **Warn about the farm's sizing where a disk kill meets a measured size.** The farm keys a
   measured size by unit, kind and compute class and **not** by whether the job has a build
   store, so a store-less run of a unit whose history is store-backed is sized at that
   history's disk peak (1.60 GiB for the unit above) and killed at about 20 GiB. That is the
   CI/CD lead's to fix in `bin/ossv-job-farm`; this watch only names the risk and tells the
   lane to pass `--disk` explicitly.

## The message for the case above

```
FARM JOB KILLED: farm/20261104t210028z-2d717d (spirv-tools/spvtools_binary_parser_fuzzer, oft-check) at 2026-11-04T21:05:53Z, OOMKilled, exit 137.
DISK, not memory: disk peak 19.66 GiB of its 4Gi (4.00 GiB) ephemeral-storage request (491%); memory peak 1.89 GiB of its 8503Mi (8.30 GiB) limit (23%). Kubernetes reports an ephemeral-storage eviction as OOMKilled, exit 137, exactly as it reports a memory kill, so the exit code names neither.
RAISE DISK to --disk 26Gi (1.3 x the 19.66 GiB peak). Do not raise memory: it was not the bound.
WARNING: this job's size came from the index (size.mode=measured). The farm keys a measured size by unit, kind and compute class and NOT by whether the job has a build store, so a store-less run of a store-backed unit is sized from store-backed history and killed. Its recent runs' disk peaks are about 1.60 GiB. Pass --disk explicitly until the farm's sizing is fixed (the CI/CD lead's, bin/ossv-job-farm).
```

And for the memory kill whose log had no processes in it:

```
FARM JOB KILLED: farm/20261104t204327z-bda302 (curl/fuzz_bufq__build, n6-unit) at 2026-11-04T21:22:12Z, OOMKilled, exit 128.
MEMORY was at its bound: memory peak 56.80 GiB of its 56Gi (56.00 GiB) limit (101%); disk peak 1.60 GiB of its 60Gi (60.00 GiB) ephemeral-storage request (3%). (the memory peak reached its limit while the disk peak did not reach its request.)
SERIALISE, probably: no per-process samples in this job's log; from cpu_last_cores 29.34 of 31 cores at the last sample, this looks like the parallel case -- many processes running when the total reached the limit. The answer is fewer at once at the same limit (-j fewer), not a bigger pod. Confirm from the next run's per-process samples before buying memory.
```

## Tests

`scripts/tests/test_farm_fail_watch.py`, 30 of them: the measured disk kill above (from the
cluster and the farm's record), a memory kill with both of its per-process arms, the
cpu_last_cores fallback on the measured curl case (parallel, one process alone, in between,
and per-process samples winning when they exist), a both-near-their-bound case with the
closer one named, and an indistinguishable case that advises no raise. Run them with
`python3 -m unittest scripts.tests.test_farm_fail_watch` from `~/.dotfiles`.
