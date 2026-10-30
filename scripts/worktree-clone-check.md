# worktree-clone-check

Finds the children of a worktrees root that are not worktrees: the ones whose `.git` is a
**directory**, so they carry an object store of their own instead of sharing the repository's.
It reports each with its size, its branch, its session and the reflog line that says how it was
made, then how many of those bytes are a second or later copy of objects another store under the
same root already holds. It exits 1 when it finds one, so it can be used as a gate.

**It deletes nothing, moves nothing and repacks nothing.** The only git it runs is `show -s`
with `GIT_OPTIONAL_LOCKS=0`; everything else is reading directories and pack indexes.

## The case it was written for

On **2026-10-30 at 15:38Z the root filesystem of sami-rocqperf reached 0 GiB free.** At 15:58:33Z
`/home/sami/.worktrees` was the largest thing on root at 30,880 MB, and effectively all of it was
one project's children.

Those children were supposed to be worktrees of one repository. As read at 15:58:33Z and handed
to this lane: five of them were not. Each held its own `.git` **directory** with a 4.9–9.2 GB
object store of the same history, one pack id (`007f3d9e`) stood in three of them, and each had
a reflog whose first line was `export from jj`, at 12:53Z, 14:09Z, 14:30Z, 14:48Z and 15:23Z. A
jj workspace export materialises an object store of its own each time, and five exports of a
5 GB repository is 25 GB of root.

This check was run against the same directory at **16:22Z on 2026-10-30**, after the owning lane
had begun clearing up, and its own reading differs from that one in detail: four of those stores
were left, two of them made by `export from jj` and two by a local `git clone` of a sibling, and
the shared pack id stood in two of them — through a single inode. It found 23 clones in all
under `~/.worktrees`, beside 12 real worktrees, and under `binary-equivalence-taiga`:

| child | `.git` | duplicated | how it was made (first reflog line) |
|---|---|---|---|
| `craft-b` | 9.16 GB | — (5.12 GB of it is one inode shared with `census`) | 2026-10-30T11:24:39Z `clone: from …/binary-equivalence-taiga/census` |
| `i2` | 5.14 GB | 5.07 GB | 2026-10-30T14:32:56Z `export from jj` |
| `craft-a` | 5.14 GB | 5.06 GB | 2026-10-30T11:31:07Z `export from jj` |
| `census` | 5.13 GB | — | 2026-10-30T11:23:49Z `clone: from …/binary-equivalence-taiga/./proof-craft` |

Four sessions, one repository, four object stores. Across the whole root the stores named
26.72 GB, occupied **21.60 GB** of root after hardlink dedup, and **10.87 GB of that was a
second or later copy** of objects another store already held.

The same parent also holds `apt-snapshot`, `ci-main-push-router`, `nightly-sync-images-yaml`,
`path-by-name` and `r1-l0`, which are real worktrees: a `.git` pointer file of a few dozen bytes
each. That is the whole difference, and it is what a `stat` per child can see.

## Why the obvious ways to count the waste are wrong

Both shortcuts were tried on this data and both gave the wrong answer, so the check does neither.

- **Pack id is not identity.** `census` and `craft-b` share pack `007f3d9e`, but `craft-a` and
  `i2` hold the same history repacked, under pack ids `b25d6e73` and `338201ce`. Grouping by pack
  id reports those two 5 GB stores as unrelated to anything and the duplication as zero. The
  check therefore reads each `.idx`: every object id, and its packed size taken from the gap to
  the next object's offset in the pack.
- **A hardlinked copy costs nothing.** `git clone` from a local path hardlinks the packs, which
  is exactly what `craft-b` is: its 5.07 GB pack and `census`'s are **one inode**, 5.07 GB on
  disk, not 10.14 GB. `du` run separately on each directory counts it twice. The check counts
  each inode once, charges it to the first store that reaches it, and gives the later ones its
  objects at zero bytes.

Stores are taken largest first, so the largest copy of a history is the original and every later
one is the duplicate. Objects are matched on the first 8 bytes of their id; a false match across
a few million objects is a one-in-millions event, and it can only move bytes into the "already
there" column, never invent duplication that is not there.

## Running it

```sh
scripts/worktree-clone-check                       # ~/.worktrees, full report, exit 1 if any
scripts/worktree-clone-check --quick               # the rule only: the names and the exit code
scripts/worktree-clone-check /some/other/root --depth 2
scripts/worktree-clone-check --follow-symlinks     # include children that are symlinks
```

`--quick` is one `stat` per child: **0.7 s** on this box's 23 clones and 12 worktrees
(measured 2026-10-30T16:22:10Z), which is what a gate wants. The full report also sizes every
clone's tree and reads every pack index: **154 s** over those 23 clones, whose trees total
37.6 GB and whose stores hold 26.72 GB (measured 2026-10-30T16:22:14Z–16:24:49Z).

A child that is a **symlink** is not followed by default and is counted at the end instead: those
point at `/mnt/nvme`, so they are not the disk this failure was about. `--follow-symlinks`
includes them.

The duplication number needs `numpy` (present in this box's `python3`). Without it the check
still lists every clone and still exits 1; it says that the duplication was not measured.

## What it reports, and what it does not decide

Per clone: total size, `.git` size and how much of it is hardlinked to another store; the branch
or the detached HEAD; the `Omp-Session` trailer of the HEAD commit when it has one, which is the
session that made the directory; the first line of the reflog, which is `clone: from …` or
`export from jj`; the packs, and how many of their bytes an earlier store already holds. Where
other worktrees point **into** a clone's store, it says so — removing that store would take them
with it.

**It does not decide what to remove.** A clone with uncommitted work, or one that hosts other
worktrees, is not a thing to delete; this check hands its owner the facts and the owner decides.

## Proof

```
$ scripts/worktree-clone-check
worktree-clone-check 2026-10-30T16:22:14Z  root /home/sami/.worktrees  depth 2
23 clone(s), 12 real worktree(s), 1 symlinked child(ren) not followed

clone  binary-equivalence-taiga/craft-b
       size    total 10.88 GB, .git 9.16 GB, 5.12 GB of it hardlinked to another store
       head    detached fcb25b7a6487 @ fcb25b7a648
       session Omp-Session: 01a16551-5d7c-72f0-bd54-d1c97d2adfdc
       reflog  2026-10-30T11:24:39Z  clone: from /home/sami/.worktrees/binary-equivalence-taiga/census
       packs   3, 8.97 GB
…
clone  binary-equivalence-taiga/i2
       size    total 5.95 GB, .git 5.14 GB
       head    detached ba212843fdbf @ ba212843fdb
       session Omp-Session: 01a16551-5d7c-72f0-bd54-d1c97d2adfdc
       reflog  2026-10-30T14:32:56Z  export from jj
       packs   1, 5.07 GB; 5.07 GB of them objects an earlier store already holds
…
clone  binary-equivalence-taiga/lanes-main
       size    total 0.58 GB, .git 0.23 GB
       head    branch main @ ee13045fa6
       session Omp-Session: 01a16a9e-a9b0-761c-97bf-28d7cf2eaac2
       reflog  2026-10-24T05:22:55Z  clone: from https://github.com/theorem-labs/binary-equivalence-taiga.git
       packs   35, 0.19 GB; 0.19 GB of them objects an earlier store already holds
       holds   5 worktree(s) point INTO this store
…
object stores: 26.72 GB named, 21.60 GB on disk after hardlink dedup
duplication: 10.87 GB is a second or later copy of an object another store under this root already holds
checked in 154.3s. This check changes nothing.
$ echo $?
1
```

The negative test builds the same shape in a scratch directory — one real worktree, one clone of
the same repository — and checks that the clone is the only child named, that a store reached
through a hardlink is not counted as duplicated bytes and that a `--no-hardlinks` copy is:
`python3 -m pytest scripts/tests/test_worktree_clone_check.py`, 5 passed, 2026-10-30T16:21:43Z.

## Where it lives

`scripts/`, not `devbox/`. `devbox/` holds the box's systemd units and the files `install.sh`
links into `~/.config/systemd/user`; this has no unit, no timer and no state. `scripts/` is on
`PATH` (`.bashrc`), so the check runs by name in any session, by hand or in a gate, the way
`scripts/pr-gate` and `scripts/docs-pr-gate` do. Tests:
`scripts/tests/test_worktree_clone_check.py`.
