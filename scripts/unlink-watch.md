# unlink-watch

Records who deletes anything under `/mnt/nvme/<user>/tmp`, the scratch tree many sessions share.

Armed with `installers/unlink-watch.sh`; runs as the user service `unlink-watch.service`, which
starts `bpftrace` through `sudo -n`. Output appends to `~/.local/state/unlink-watch.log`:

```
20:32:24Z unlink pid=3389126  bash   /sami/tmp/sh-thd.R5Pp1I
```

## Why

On 2026-11-07 the whole contents of `/mnt/nvme/sami/tmp/CICDLead` were deleted between 20:05:20Z
and about 20:08Z — a live session's own working directory, scripts, logs and a running driver's
work tree. **Nothing on the box could say what did it.** The evidence that could be gathered was
all negative: no container died or was destroyed in the window (`docker events`), `box-cleanup`
had run at 20:00:07Z and reclaimed 0 B with every reaper skipped on its interval, no other prune
timer was due, and a box removal cannot touch a `--dir` by construction. It took the owning
session reading its own worker's transcript to find the `rm -rf`.

The kernel keeps no record of an unlink, and **inotify reports the path but not the process**, so
it cannot answer "who".

## How

It attaches to the LSM path hooks, `security_path_unlink` and `security_path_rmdir`, where both
the directory and the caller are in hand, and prints the time, the pid, the command and the path.

**Two kernel-side limits shaped it, and both are load-time failures rather than quiet ones:**

- `path()` is refused on this kernel's bpftrace (`BPF_FUNC_d_path not available`, and it is
  allowed only on `kfunc` probes anyway), so the directory is identified by **walking three
  dentries up and comparing names**: a delete is reported when it sits under a directory `tmp`
  whose own parent is `sami`. That is this tree and nothing else on this machine, but it is a
  name match, not a path match — a `…/sami/tmp/` anywhere else would also match.
- Three 64-byte strings **exceed the BPF stack limit** and the program will not load. The unit
  sets `BPFTRACE_MAX_STRLEN=32`.

The installer refuses when `kfunc:security_path_unlink` is not in `bpftrace -l`: a kernel without
`CONFIG_SECURITY_PATH` would let the program load and report nothing, which reads exactly like a
machine where nobody deletes anything.

## Checking it

There is no unit test: the logic is a BPF program whose only honest exercise is a real delete.
Use a canary, which takes five seconds:

```bash
mkdir -p /mnt/nvme/$USER/tmp/canary && touch /mnt/nvme/$USER/tmp/canary/probe
rm /mnt/nvme/$USER/tmp/canary/probe && rmdir /mnt/nvme/$USER/tmp/canary
tail -3 ~/.local/state/unlink-watch.log
```

Two lines, naming `rm` and `rmdir` with your own pid. If the log is silent, the program is
attached to nothing — check `systemctl --user status unlink-watch` and the `bpftrace -l` line in
the installer before believing the tree is quiet.

## What it does not do

It names the process, not the session: a pid that has already exited cannot be attributed, and
there is no box lookup here as there is in `token-tripwire`. Add one (`/proc/<pid>/cgroup` plus
`docker inspect`) if attribution after the fact turns out to matter more than the pid at the time.
