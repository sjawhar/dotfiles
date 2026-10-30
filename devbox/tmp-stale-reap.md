# tmp-stale-reap

`/tmp` is on the **root** filesystem of this box, and it is the one scratch area that no `TMPDIR`
redirect reaches. This timer removes a top-level entry of it once no process holds it and
nothing in it has been written for 48 hours.

| | |
|---|---|
| Script | `devbox/tmp-stale-reap` (`--dry-run` by default, `--apply` removes) |
| Units | `devbox/tmp-stale-reap.service`, `.timer` (hourly), armed by `devbox/install.sh` |
| Knobs | `TMP_REAP_ROOT`, `TMP_REAP_AGE_HOURS`, `TMP_REAP_KEEP`, `TMP_REAP_PROBE_SECONDS`, `TMP_REAP_IO_PRESSURE_MAX` |

## Why it exists

The root filesystem of sami-rocqperf reached **0 GiB free at 15:38Z on 2026-10-30**
(dispatch://INFRA-6). `/tmp` held 26,457 MB of it.

`TMPDIR` was already redirected: `scripts/agent-session` and `.bashrc` put every agent session
and every interactive shell on `/mnt/nvme/<user>/session-tmp/<id>`, and `session-scratch-reap`
removes that when the session ends. So **no environment variable would have moved a byte of
those 26 GB.** What is in `/tmp` is there because something wrote the path `/tmp/<name>` by hand:
a tool's default, an agent typing a scratch path, a container, a daemon, or a process that
started before the rc ran.

The box's own cleaner never fires on it. `/usr/lib/tmpfiles.d/tmp.conf` is `D /tmp 1777 root root
30d` and `systemd-tmpfiles-clean.timer` is active (next 2026-10-31 14:11Z, read 16:10:50Z), but
the **oldest thing in `/tmp` was 4.98 days old** (`mtime` 2026-10-25T16:31:44Z, read 16:10:33Z).
A 30-day rule on a directory nothing in reaches five days is not a cleaner.

## What was actually in there, 2026-10-30

`du -x --max-depth=1` over `/tmp`, 16:09:11Z → 16:13:26Z, and `stat` of every entry at 16:10:33Z:

| | |
|---|---|
| Size | 26,457.1 MB |
| Entries | 17,454 top level |
| Owners | 17,428 `sami`, 24 `root`, 2 `dd-agent` |
| Ages (`mtime`) | 137 under 1 h, 802 under 6 h, 4,077 under 24 h, 9,165 under 2 d, 3,003 under 3 d, 274 under 7 d, **none over 5 d** |

The largest entries, with the age of the newest file anywhere inside each (`find -printf '%T@'`,
16:21:21Z) — every one of them is an agent's working clone or review tree, not a tool's cache:

| Entry | Size | Newest file inside |
|---|---|---|
| `warmset-trees.bak` | 1,426.7 MB | 49.6 h |
| `ck` | 1,382.8 MB | 52.0 h |
| `rv357` | 1,020.3 MB | 13.3 h |
| `a6-controls.json` | 1,008.0 MB | 49.8 h |
| `verify-this` | 817.2 MB | 9.1 h |
| `t13-review` | 660.6 MB | 64.2 h |
| `llv2` | 639.3 MB | 12.2 h |
| `tc_all.pkl` | 525.6 MB | 46.3 h |
| `sbcode-tree` | 506.2 MB | 4.2 h |
| `fwenv` | 459.2 MB | 36.5 h |

### What is still in use

One pass over `/proc` at 16:10:22Z — every process's `cwd`, `exe`, `root`, open fds, mapped files
and `TMPDIR`/`AGENT_SESSION_SCRATCH` — found **8 top-level entries of `/tmp` held by a live
process**, and no more:

| Entry | Held by |
|---|---|
| `bet64-s2` | pids 3075582, 3170981 — cwd and an open file |
| `bet432-3ab-main` | pid 3129324 — an open file |
| `probe19` | pids 3162388, 3162453, 3162461, 3227619 — cwd and open files |
| `ctx-memory-health-first-pass-3001308-0` | pids 3015476, 3015958, 3016100, 3016125 — cwd and open files |
| `numberb-warm-ci.txt` | pids 2561003, 2563300 |
| `p3b-copy.err` | pids 3890768, 3890959 |
| `#137550 (deleted)` | pids 2878222, 2878227, 2878292, 2878296 — an unlinked inode, not a name on disk |
| `.tmpaudit-du.txt` | the audit above |

**What cannot be attributed.** `/proc/<pid>/fd` and `/proc/<pid>/maps` of another user's process
are unreadable from here, so a `root` or `dd-agent` process holding something open in `/tmp` is
invisible to this measurement and to the reaper. The 26 entries those two users own are the only
ones that could hide behind that, and the reaper never considers another user's entry at all.
Everything else in `/tmp` — 17,428 entries of this user — is held by nothing this script can see.

## The decision: liveness first, age only as a backstop

`skill://disk-hygiene`: age may keep a path longer, never mark one dead. An entry is removed only
when **all** of these hold, and survives whenever any one of them is merely unsure:

1. **This user owns it.** Never another user's, so never root's and never a daemon's.
2. **It is a plain file or a plain directory.** Never a socket, fifo, device or symlink.
3. **Its name is not on the keep list** (below).
4. **No process holds it** — not as `cwd`, `exe` or `root`, not an open fd, not a mapped file,
   and not named in `TMPDIR` or `AGENT_SESSION_SCRATCH`. One pass over `/proc` answers this for
   every candidate at once; never an `lsof` per candidate, which walks the whole tree.
5. **Nothing in it, at any depth, is a unix socket.** A daemon may still be serving it.
6. **Nothing in it, at any depth, belongs to another user.** A container that runs as root
   writes root-owned files into a directory this user made: `/tmp/r22651/.coq-native` on
   2026-10-30, mode 755 root:root under a `sami`-owned entry. This script can neither read
   root's `/proc` to see whether root is still using them, nor unlink them — the apply run of
   16:35Z half-removed that entry and then failed on them. Such a tree is kept and named.
7. **Nothing in it, at any depth, has an `mtime` or a `ctime` inside the window.** The walk stops
   at the first file that vetoes the entry, so a live tree costs almost nothing. A walk that runs
   past `TMP_REAP_PROBE_SECONDS` (120 s) or fails keeps the entry: a timeout is not proof.

The whole tree is walked, not the top-level entry's own timestamps, because the top-level stat
lies in the dangerous direction. Measured 16:14:48Z → 16:16:43Z:

| Entry | Top-level stat says | Newest file inside says |
|---|---|---|
| `warmset-trees.bak` | 107.7 h | **61.0 h** |
| `llv2` | 27.1 h | **12.1 h** |

`atime` is deliberately ignored. Any scanner rewrites it: this box's own `du` of `/tmp` at
16:09Z moved almost every directory's `atime` to "now", and with `atime` in the age the same
corpus reported 0.91 GiB over 24 h instead of 19.27 GiB (16:13:43Z vs 16:14:36Z). `trivy` runs on
this box too. An `atime` rule is a rule a backup job can switch off.

## Why 48 hours

| Window | Entries over it | Would free |
|---|---|---|
| 24 h | 12,443 | 18.64 GiB |
| 36 h | 10,113 | 16.92 GiB |
| **48 h** | **3,299** | **12.94 GiB** |
| 60 h | 1,936 | 3.98 GiB |
| 72 h | 294 | 0.07 GiB |
| 96 h | 0 | 0 |

(Age = newest `mtime` or `ctime` anywhere inside the entry, this user's entries only, 16:21:21Z.)

Two measurements fix the window between those rows.

- **The floor is 16.7 h.** That is the longest a tree a live process was holding had gone
  untouched: `/tmp/bet432-3ab-main`, held open by pid 3129324, newest file inside 16.7 h old
  (16:16:17Z). An agent that comes back to a clone after a long run is normal here, and the
  longest-running process of this user was 74.3 h old at 16:16Z. 48 h is **2.9×** that observed
  floor, on top of the `/proc` check that already protected that entry; 24 h would be 1.4×.
- **The ceiling is 72 h.** Nothing in `/tmp` reaches five days, so a 72-hour window frees
  0.07 GiB and a 96-hour one frees nothing: above 72 h this unit is the same as no cleaner, which
  is exactly what the 30-day `tmpfiles` rule already is.

48 h is the longest window that still reclaims a useful share of `/tmp` (half of it), and it is
three times the longest idle a live holder has shown. Raise it for a run with
`TMP_REAP_AGE_HOURS=72 devbox/tmp-stale-reap --dry-run`; the value takes a fraction, which is how
the test below gets a window of seconds.

## What it will never touch

- **Anything a live process holds** — see rule 4. Not its cwd, not an open fd, not a mapped
  shared object, not a `TMPDIR` or `AGENT_SESSION_SCRATCH` that names it.
- **Anything owned by another user.** `root`'s `systemd-private-*` trees, `dd-agent`'s files,
  `trivy`'s `/tmp/trivy-*`: never candidates, whatever their age.
- **Any entry that is not a plain file or directory**: a socket, a fifo, a device, a symlink.
- **Any directory holding a unix socket at any depth**, at any age.
- **Any directory holding another user's file at any depth**, at any age — a container that ran
  as root, a `sudo` left behind. It cannot be checked for liveness and it cannot be unlinked.
- **These names, whoever owns them and however old they are:** `.X11-unix`, `.XIM-unix`,
  `.ICE-unix`, `.font-unix`, `.Test-unix`, `.XDG-*`, `systemd-private-*`, `snap-private-tmp`,
  `tmux-*`, `ssh-*`, `dbus-*`, `gpg-*`, `.gnupg*`. The X and Wayland sockets, systemd's private
  per-service trees, the tmux server sockets and the ssh-agent and gpg-agent directories are the
  ones this user owns and a socket's directory can otherwise look exactly like a stale one.
  `TMP_REAP_KEEP='pattern …'` adds to the list for a run. The patterns over-keep on purpose: the
  dry run of 2026-10-30 kept `/tmp/tmux-fix`, somebody's scratch directory, on `tmux-*`. A
  scratch directory that survives costs disk; a server socket that does not costs a session.
- **Anything outside `/tmp`'s own filesystem** (`find -xdev`) and anything below the top level: it
  removes whole entries of `/tmp` or nothing, never a file out of someone's tree.
- **Anything at all, when `/proc/pressure/io` `full avg10` is over 20**, because deleting under IO
  pressure hangs every shell on the box. The next run is an hour away.
- **Anything, on a hand run.** `devbox/tmp-stale-reap` with no argument reports and removes
  nothing; only `--apply` removes, and only the service passes it.

## Proof

A scratch root (`$T=/mnt/nvme/sami/tmp-reap-proof`) with one entry of every kind, built
16:45:32Z, a window of 120 s, and a live process holding `$T/held/data` open through `exec 3<`.
`$T/young-1mb` and the file under `$T/deepfresh/sub` were written 130 s after the rest;
`$T/withroot/sub/file` is root-owned; `$T/readonly/sub` is mode 555, which plain `rm -rf`
cannot empty.

```
$ TMP_REAP_ROOT=$T TMP_REAP_AGE_HOURS=0.0333 devbox/tmp-stale-reap          # 16:47:42Z, no --apply
tmp-stale-reap: keep $T/.X11-unix (name matches the keep pattern '.X11-unix')
tmp-stale-reap: keep $T/young-1mb (changed 0.0h ago, inside the 0.0333h window)
tmp-stale-reap: keep $T/alink (a 'l', not a plain file or directory)
tmp-stale-reap: keep $T/sockdir (it holds a unix socket: $T/sockdir/s.sock)
tmp-stale-reap: keep $T/deepfresh (written inside the 0.0333h window: $T/deepfresh/sub/file)
tmp-stale-reap: would remove $T/readonly (9.0 KB, 0.0h since the newest file in it, no live process)
tmp-stale-reap: would remove $T/stale-2mb (2.0 MB, 0.0h since the newest file in it, no live process)
tmp-stale-reap: keep $T/withroot (it holds another user's file: $T/withroot/sub/file, uid 0)
tmp-stale-reap: keep $T/held (live: pid 128867 via an open file)
tmp-stale-reap: $T: 2 would be removed (2.0 MB), 2 live, 2 written inside the 0.0333h window,
  1 on the keep list, 0 another user's, 1 holding another user's files, 1 not a plain file or
  directory, 0 unreadable; 0 failed

$ TMP_REAP_ROOT=$T TMP_REAP_AGE_HOURS=0.0333 devbox/tmp-stale-reap --apply  # 16:47:48Z
tmp-stale-reap: removed $T/readonly (9.0 KB, 0.0h since the newest file in it, no live process)
tmp-stale-reap: removed $T/stale-2mb (2.0 MB, 0.0h since the newest file in it, no live process)
…the same five keeps, and: 2 removed (2.0 MB) … 0 failed

$ ls -A $T
.X11-unix  alink  deepfresh  held  sockdir  withroot  young-1mb
```

The two stale entries went, including the one `rm -rf` alone could not empty. The entry written
0 s ago, the one whose only recent write is three levels down, the socket directory, the
keep-list name, the symlink, the tree holding a root-owned file and the entry a live process
holds all stayed, and pid 128867 still had its descriptor afterwards.

The gates that only a loaded box shows have fired on this box too: a run at 16:42:44Z stopped on
`io pressure full avg10=22.33 is over 20; nothing removed this run`, and a tree whose
subdirectory was mode 000 was kept with `the walk of it failed, exit 1`.

## The first real run

`systemctl --user start tmp-stale-reap.service`, 16:35:25Z → 16:41:30Z, 2 min 57 s of CPU:

```
tmp-stale-reap: /tmp: 3246 removed (11.4 GB), 0 live, 14188 written inside the 48h window,
  15 on the keep list, 15 another user's, 8 not a plain file or directory, 0 unreadable;
  1 failed; 27G free on /tmp
```

`df -BG /` read **14G available before and 27G after**, on a box where eleven other agent
sessions were writing throughout. Nothing a process held was touched: `0 live` is there because
every entry `/proc` showed a holder for was also inside the 48-hour window, and so was never a
candidate.

The one failure was `/tmp/r22651`, the root-owned `.coq-native` above; the run that found it did
not yet have rule 6 and half-removed the entry before failing on those four files. What is left
is a 58,771-byte stub holding four root-owned Coq objects from 2026-10-27, which this user
cannot `chmod` or unlink (`Operation not permitted`, 16:43:23Z). It stays. Rule 6 is what keeps
the next run from touching another one like it, and keeps the service out of `failed` every
hour over something it can never remove.

## Running it by hand

```sh
devbox/tmp-stale-reap                    # decide and print, remove nothing
devbox/tmp-stale-reap --dry-run          # the same thing, said out loud
devbox/tmp-stale-reap --apply            # remove
journalctl --user -u tmp-stale-reap.service -n 200
```

A hand run names **every** entry it keeps and why, which is 17,000 lines on this box; pipe it
through `grep 'would remove'` for the other half. The service names only the removals and the
keeps a complaint is ever about (live, keep list), so the hourly journal stays readable.

## Rollback

```sh
systemctl --user disable --now tmp-stale-reap.timer
```

Nothing else changes: the script is the only thing that removes anything, the units are links
from `~/.dotfiles/devbox`, and `devbox/install.sh` arms the timer again on the next run.
