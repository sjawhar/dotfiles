# Finding where the space went, and what writes now

`S` and `SKILL` are as set in SKILL.md. Each command here answers one question and prints what its
number does not measure. On 2026-10-30 two disks on this box filled while nine readings taken that
afternoon were each true of something other than the question asked.

## Where the space is

- Rank directories: `python3 $S sizes --max-depth 2 --top 15 <roots>`. It runs one `du` over every
  root. Each row's `own GB` holds none of the bytes of the listed directories below it, so the rows
  add up. Never add up `du --max-depth=N | sort -rn | head`: that list holds a parent and its
  children both, and its sum passes the total. Measured on `~/.cache`, 2026-10-30: the top 15 lines
  of the pipeline came to 25.01 GB against a true total of 11.31 GB (221%), where
  `sizes --max-depth 3 --top 15` came to 6.74 GB (60%).
- Two trees that share files: add `--hardlinks`. du counts an inode once in one run, and again in
  the next run, so one run per tree adds the shared bytes to each tree. Measured on two hardlink
  copies of one 1.94 GB tree, 2026-10-30: one du per tree came to 3.87 GB, one du over both to
  1.99 GB. Removing one copy frees only what the other does not share, here 0.05 GB.
- A tree too large to walk at once:
  `sudo python3 $SKILL/scripts/disk_attribution.py --out <file.tsv> <roots>`. Run it under
  `systemd-run` when it can outlive a tool call, because a tool timeout kills it. Never run one
  `du` of everything: it can run for hours.
- What appeared: `python3 $S recent --since 2h --top 15 <roots>`. It counts every file, whatever
  its size, and dates each one by ctime. `find -size +500M` does not see a flood of small files,
  which is how one of the two disks filled on 2026-10-30. mtime cannot date an arrival: gzip,
  `cp -p`, `tar -p` and `rsync -a` all copy it from the source.
- Deleted-but-open space: count each `lsof +L1` path once, because `lsof` prints a file open in
  several processes once for each of them. Such a file still occupies the disk and `du` cannot see
  it, so a hunt whose numbers do not add up is usually this; one held a 3.3 GB pack until its
  process exited. Let the process exit rather than deleting more.
- Broken links: `python3 $S dangling <roots>` finds the symlinks whose target is gone
  (`find -xtype l`). A sweep of `~/.cache` and `~/.local` on 2026-10-30 found 3,679 of them. They
  hold no data: they show where a deleted tree left the links that point at it.

## What writes now

- Rank the writers: `sudo python3 $S writers --seconds 5 --top 15`. It names each process, its
  write rate over the interval and its working directory, with the same counter over the whole
  life of the process beside it. A short sample is a burst and not a rate, so where the two columns
  disagree, run it again. Without `sudo` this uid cannot read the counters of other users'
  processes, and the footer counts the ones it could not read.
- Name the file: `python3 $S fdwrites --seconds 5 --pid <pid>`. `/proc/<pid>/io` counts bytes with
  no file and no filesystem attached, and it counts a rewrite of the same bytes again, so it is an
  upper bound on what any disk gained. `df` before and after is what a filesystem gained.
- What holds the box: `python3 $S cpu --seconds 5`, grouped by command name and not by process.
  Measured 2026-10-30: 150 `python3` processes on 36.9 of 64 cores (58% of the box), which a list
  of single processes hides.
- A watcher reports the extreme of the samples it took, not the extreme. Give the interval with the
  number ("the lowest of 60 one-minute samples"). For a bound that misses nothing, read a counter
  that accumulates: the `total=` field of `/proc/pressure/io`, the `df` the apply ledger writes
  after each action, or an ENOSPC in the kernel log.
- Four readings on one limb of a cycle are not a trend. A shared volume sawtooths as lanes write
  and delete: about 25 to 30 GB of amplitude every 5 to 8 minutes, with flat troughs. Four
  readings over twelve minutes on one descending limb gave a 309 GB/h fill and a predicted full
  disk fifteen minutes out, and the volume then gained 30.2 GB in 407 s and ended higher than it
  began. A rate needs a period longer than the thing's own cycle, or it measures the phase you
  sampled: before declaring a trend, take one reading a full cycle later, or show that the FLOOR
  is moving. The steady floor is the number worth acting on.
- Total write throughput far larger than any change in free space means write-then-delete churn
  rather than accumulation, so there is no runaway writer to find: 164.5 MB/s across dozens of
  processes, none above 61.5 MB/s, on a volume whose net was about zero.

## Two deletions that took more than they were given

- `find` tests the starting directory as well as its contents, so give every `find … -delete` or
  `find … -exec rm` a `-mindepth 1`. On 2026-10-31
  `find /mnt/nvme/sami/tmp -maxdepth 1 -name 'tmp*' -exec rm -rf {} +` matched
  `/mnt/nvme/sami/tmp` itself and deleted other sessions' scratch there for five minutes
  (dispatch://INFRA-893).
- A prefix is not ownership. An agent clearing its own pid-suffixed scratch with `rm -f /tmp/mt.*`
  destroyed two files of somebody else's that shared the prefix, 12,145 and 415 bytes, with no
  process holding them and nothing to recover. Name each entry you delete, or delete a directory
  you made yourself.
- The dangerous pattern is not only a prefix you chose, it is a prefix ANYONE would choose:
  `base_`, `now_`, `merged_`, `v1`, `emit`, `tmp` are what every session on a shared box names a
  scratch file. Three lanes globbed `/tmp` within one hour of each other. Where a cleanup must use
  a pattern, list the matches and read them before you remove any: `ls` costs a second and turns a
  guess into a decision, and `rm` prints nothing it took.
