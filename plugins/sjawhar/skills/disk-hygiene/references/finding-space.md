# Finding where the space went, and what writes now

Read this before you size, rank or watch disk use, or before you delete by a pattern or with `find`. `S` and `SKILL` are set in SKILL.md. Each instrument prints what its number does not measure; read that line.

## Where the space is

- Rank directories with `python3 $S sizes --max-depth 2 --top 15 <roots>`, never by adding up `du --max-depth=N | sort -rn | head`, whose rows overlap.
- Rank by allocated blocks, never apparent size: a sparse file (a test fixture, an agent box disk image) claims bytes it does not hold. `du` is allocated by default; `du -b`, `du --apparent-size`, `find -printf %s` and `stat -c %s` are apparent. For a per-file figure, `find -printf '%b'` gives 512-byte blocks.
- A tree too large to walk at once: `sudo python3 $SKILL/scripts/disk_attribution.py --out <file.tsv> <roots>`, under `systemd-run` when it can outlive a tool call.
- Never run one `du` of everything: `du -d` and `du -t` bound what it prints, not what it reads, so it still walks the whole disk. Size suspected paths one at a time, as a unit that survives your session and loses every IO race: `systemd-run --user --unit=<name> -p Nice=19 -p IOWeight=10 bash -c 'du -xsm <paths> > <outfile>'`. Ask each session for the `du -xsm` of its own paths rather than walking everyone's trees.
- What appeared: `python3 $S recent --since 2h --top 15 <roots>`. It counts every file whatever its size and dates it by ctime. `find -size +500M` misses a flood of small files, and mtime cannot date an arrival: gzip, `cp -p`, `tar -p` and `rsync -a` copy it from the source.
- Deleted but open: `lsof +L1`, counting each path once (it prints a line per process). `du` cannot see such a file, so totals that do not add up are usually this. Let the process exit rather than deleting more.
- Broken links: `python3 $S dangling <roots>`. They hold no data; they show where a deleted tree left links to it.
- Measure a cleanup by `du` of the paths you removed, before and after, not by a `df` delta: other sessions free and write in the same window. Where you read `df`, read `df <path>`; a removal under another mount leaves `/` unchanged.

## What writes now

- Rank writers: `sudo python3 $S writers --seconds 5 --top 15`. Without `sudo`, other users' processes are unreadable.
- Name the file a writer grows: `python3 $S fdwrites --seconds 5 --pid <pid>`.
- What holds the CPU: `python3 $S cpu --seconds 5`, grouped by command name, where a fan-out of one name hides.
- A write sample says who writes now, not who holds the space. Rank owners by each session's `du -xsm` of its own paths; rank a process by sizing the files it holds open twice, minutes apart.
- A watcher reports the extreme of its samples, not the extreme. Give the interval with the number ("the lowest of 60 one-minute samples"). For a bound that misses nothing, read a counter that accumulates: `total=` in `/proc/pressure/io`, the `df_used_gb` of each apply ledger line, or an ENOSPC in the kernel log.
- A shared volume sawtooths as lanes write and delete. A rate needs a window longer than one cycle: take a reading a full cycle later, or show that the floor moves.
- Write throughput far above any change in free space is write-then-delete churn, not a runaway writer.

## Deleting by pattern or with `find`

- Give every `find ... -delete` or `-exec rm` a `-mindepth 1`: `find` tests the starting directory too, and has deleted the directory it was told to clean inside.
- A prefix is not ownership. `base_`, `now_`, `merged_`, `v1`, `emit` and `tmp` are names every session picks. Name each entry you delete, or delete a directory you made. Where a cleanup must use a pattern, `ls` the matches and read them before you remove any: `rm` prints nothing it took.
