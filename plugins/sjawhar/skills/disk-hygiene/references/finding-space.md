# Finding where the space went, and what writes now

Read this before you size, rank or watch disk use, or before you delete by a pattern or with `find`.

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

- Where the space usually is, largest first: dangling images, buildkit cache with leaked leases, unused tagged images, orphaned sandboxes, `/tmp` families, workspaces. Workspaces free less than they look, because `uv` venvs hardlink into `~/.cache/uv` where the cache shares their filesystem. `$S sizes --hardlinks <tree> <cache>` measures the shared bytes: sizing the two trees in two commands counts them in both.
- Do not size a candidate to decide its deletion: under IO load sizing is slow. Measure the result by the directory you removed, before and after, not by `df`: when several sessions clear at once the filesystem moves gigabytes between consecutive reads, so `df` tells you about the box and not about your cleanup. Where you do read `df`, read the filesystem the path is on, which `df <path>` gives: a removal under another mount leaves `/` unchanged.
- Never run one `du` of everything: it can run for hours. To find where the space went, what writes now, or what appeared in the last hours, use this file. Each instrument there is a named subcommand of `$S`, and each prints what its number does not measure.
- `du -d` and `du -t` bound what `du` PRINTS, not what it READS: to total a directory it stats every inode under it, so a bounded `du` over a whole multi-terabyte array is still a whole-array walk. On 2026-11-01 `du -x -d2 -t 2G /mnt/nvme ...` ran over two hours on the 3.2 TB array without finishing. So size the paths you suspect, one at a time: `du -xsm <path>` on a named directory, run as a unit so it survives your session and loses every I/O race to real work, `systemd-run --user --unit=<name> -p Nice=19 -p IOWeight=10 bash -c 'du -xsm <paths> > <outfile>'`. Ask each session for its own `du -xsm` rather than walking everyone's trees from one place, and never run two walkers at once (the rule above).
- A file with link count 2 or more frees nothing when one of its names is deleted. A cache and a data directory sharing inodes, or mutation copies made with `os.link`, look twice their real size in a per-path `du`. Measure every path that may share inodes in one `du` call, which counts each inode once, before you promise anyone the space back.

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

- A write sample says who writes now, not who holds the space. `/proc/<pid>/io` `write_bytes` counts rewrites and deleted output on every filesystem, so the top writer in a 15-second sample is often a build that deletes as it goes. To rank owners, ask each session for the `du -xsm` of its own paths; to rank a process, sample the size of the files it holds open twice, minutes apart. On 2026-11-01 a figure of "4.21 GB written in four minutes" by the omp transcript tree was the size of every file whose mtime moved; its real growth was 0.49 GiB/h.

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
- Never glob a shared directory: a prefix is not ownership. Name each entry you delete, or delete a directory you made. `rm -f /tmp/mt.*`, cleaning one agent's own pid-suffixed scratch, destroyed two unrecoverable files of somebody else's that shared the prefix. The box tree needs the same rule for a second reason, its live bind-mount sources: [box-mounts.md](box-mounts.md).
- Give every `find ... -delete` or `find ... -exec rm` a `-mindepth 1`, because `find` tests the starting directory too and has deleted the directory it was told to clean inside (this file).
