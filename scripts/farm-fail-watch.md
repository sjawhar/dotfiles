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
   - for a memory kill, `pod-memory-series <pod> <start> <end>`: the node's non-evictable
     memory series, using the existing helper rather than a separate Monitoring query.
     The window runs from the job container's start to its end when known; without a
     start it covers the six hours ending at the kill. Only shape and rate are retained.

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
   - memory: first check this run's series for sustained growth. If it keeps climbing,
     withhold the multiplier and every `--memory` figure; explain the rate and how little
     time extra memory buys instead. Otherwise serialise when the largest single process
     held under half the limit; the remaining sizing advice uses **only the farm's recorded
     peak**, at 1.3 x, never a series height.

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

     **The series check does not require history**, so it also works for shell-offload jobs
     absent from the farm's size index. A sustained climb means at least ten minutes of
     samples, no fall more than **0.10 GiB below the running high**, and at least **0.25 GiB
     gained in every five-minute window**, ending at the first sample at or after that
     window's end. Gaps may be at most **125 s**: one missed 60 s sample plus 5 s of jitter.
     The last sample must also be within 125 s of the recorded kill. Small dips and brief
     pauses do not hide a climb, but a five-minute plateau, a substantial fall or a single
     late spike does not qualify. These are conservative evidence thresholds, not proof
     that a finite trace can never level off.

     A climbing series **replaces** sizing advice, whatever the history or process samples
     say: "any limit you pick is a date, not a fix." The rate is the first-to-last gain over
     elapsed minutes. Time bought is **extra limit divided by that rate**, not remaining
     headroom divided by a series height: at 1.8 GiB/min, 64Gi to 80Gi buys about 9 more
     minutes and 64Gi to 128Gi about 36. Those are slope-only illustrations, not predictions
     of when the container will die. Under gVisor the node charges sandbox memory to the
     container; a series reading can exceed its own limit. **Trust the shape, never the
     height. Never turn a series reading into a size.**

     With no pod name, no points, an unreadable helper/API, too little coverage, large gaps
     or stale samples, the advice says **"Memory shape unknown"** and why. Any accompanying
     farm-peak multiplier is conditional on checking bounded growth before buying memory.
     An adequately sampled trace that does not meet the climb test says "Series checked";
     it does not prove bounded growth or rule out a spike shorter than the 60 s sampler.
     For multi-container pods only the job container is used; a single differently named
     shell container is also accepted. Ambiguous streams are unknown, never combined.

     The existing historical warning remains independent: a recent run of the same unit
     and kind peaking above this run's limit warns that the peak is what it was given,
     not what it needs. It comes from `job.json`'s `size.recent_peaks` and does not override
     serialising. A checked series never cancels that warning.

     With neither test available, the message says the serialise test could not be applied
     rather than skipping it silently.
   - disk: `--disk` at 1.3 x the measured disk peak, and "do not raise memory: it was not
     the bound".
   - both: disk advice and memory advice independently; a climbing memory series still
     withholds the memory figure.
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
SERIALISE, probably: no per-process samples in this job's log; from cpu_last_cores 29.34 of 31 cores at the last sample, this looks like the parallel case -- many processes running when the total reached the limit. The answer is fewer at once at the same limit (-j fewer), not a bigger pod. Confirm from the next run's per-process samples before buying memory. Memory shape unknown: series not read; bounded growth has not been checked. Read the series before buying memory.
```

## Tests

`scripts/tests/test_farm_fail_watch.py` covers disk, memory, both and indistinguishable
resource verdicts, the per-process and cores-based serialising advice, and the existing
history warning. `AJobWhoseMemoryIsUnboundedIsNotToldToBuyMore` also covers history-free
climbs, small dips and a missed sample, plateaus and substantial falls, unknown shapes,
gVisor-inflated readings, separate containers, and collection through the existing helper.
The helper itself is exercised with fake `gcloud`/`curl` commands for a climb, absent points
and an API refusal; no live cloud access is needed.

Run `python3 -m unittest scripts.tests.test_farm_fail_watch` **from a copy outside the live
checkout**, with `scripts/farm-fail-watch`, `scripts/pod-memory-series` and the test file in
their usual relative locations.
