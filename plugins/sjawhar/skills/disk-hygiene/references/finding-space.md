# Finding where the space went

`SKILL` is as set in SKILL.md.

- Where the space went: `sudo python3 $SKILL/scripts/disk_attribution.py --out <file.tsv> <roots>`. Run it under `systemd-run` when it can outlive a tool call, because a tool timeout kills it. Never run one `du` of everything: it can run for hours.
- What fills the disk now: read `write_bytes` in `/proc/*/io` twice, 15 s apart, and sort by the difference. That names the processes writing now, which a size walk cannot.
- Deleted-but-open space: count each `lsof +L1` path once, because a file open in several processes is listed once per process.
