# Shared git store: forgetting, removing and repairing worktree entries

Read this before you forget a jj workspace, remove a git worktree, or repair one whose entry is missing. Many boxes and sessions share one git store, so a change to one entry can break another session's checkout.

- Never run a repo-wide `git worktree prune`: from a box, it deletes every other box's worktree entry. `~/.dotfiles/shims/git` refuses a bare prune.
- Drop your own entry with `jj --ignore-working-copy --config fsmonitor.backend=none workspace forget NAME` or `git worktree remove <path>`, so that only that one entry goes.
- Remove a worktree with `git worktree remove`, never by deleting its directory. `~/.dotfiles/shims/git` locks every worktree it adds (AGENTC-431), and that lock outlives the directory, so a deleted directory leaves a locked registration in the shared store that nothing cleans and no prune may touch. Two such registrations survived from 2026-11-01 08:30Z until their owner unlocked and removed each by exact path.
- **The exception to the line above, and it reverses it: an ORPHAN whose name has been recycled must be removed with `rm -rf`, never `git worktree remove`.** The danger is the **bare NAME**, not the path. Measured on git 2.43.0 against a throwaway repo built to reproduce the condition (2026-11-07; an orphan directory whose `.git` names an admin entry that now serves a different, live worktree):

  | form | result |
  |---|---|
  | `git worktree remove <orphan PATH>` | refused, `rc=128`, `fatal: '…' is not a working tree`; the live worktree is untouched |
  | `git worktree remove --force <orphan PATH>` | same refusal |
  | `git worktree remove <bare NAME>` | **`rc=0`, silent: the live worktree is deregistered AND its directory is deleted** |
  | `git worktree remove --force <bare NAME>` | same destruction |

  **The refusal of the path form is not a clean bill of health, and must not be read as one**: an agent that tests with the path, sees `not a working tree` and concludes the warning is overstated will then reach for the name, which is the form that destroys. Check before removing anything that looks abandoned: read `<orphan>/.git` for its admin directory, then read that directory's own `gitdir` file. If it does not name your path, the entry is not yours any more. Measured the same day in `/home/sami/src/remeasure-1107/.worktrees/`, 16 directories left by a session that ended: **0 were still registered to their copy and 13 had been recycled** to live worktrees under the shared task repository's own `.worktrees/`. Another lane met the same thing an hour earlier — its `wt84` and `wt89` entries had been taken over by two live trees — and removed the orphans with `rm -rf`, leaving the shared store untouched. That is the correct route, and the lock argument above does not apply, because a recycled entry is not the orphan's registration to leave behind.
- **A recycled entry also makes git lie about the orphan's contents.** The orphan's `.git` points at an admin directory that now serves another worktree, so `git status`, `git log` and `git diff` run inside the orphan answer about **that** tree: a clean status there is a true statement about something else. The usual safety check before deleting a worktree — is its head on a remote, is the tree dirty — returns a confident wrong answer rather than an error. The cheap check that does work is a file-level comparison against the live tree: `diff -rq <orphan> <live> -x .git -x __pycache__ -x .venv -x node_modules`. An empty result means the orphan holds nothing the live tree does not, and it settled three directories in one command on 2026-11-07. Where there is no live counterpart, archive before removing.
- If entries go missing (a git that predates the `~/.dotfiles/shims/git` prune refusal, or git run outside the shim), run `git -C <ws> rev-parse --git-dir` on each kept workspace or checkout. "is not a working tree" is the same loss.
- Before `jj workspace forget X`, run `cat X/.git` and make sure no other live worktree's `.git` names the same admin dir. If one does, the forget breaks that sibling: its git fails with `fatal: not a git repository`, while its jj keeps working, so nobody notices.
- To restore a missing entry, make `<store>/.git/worktrees/<name>/` with three files:
  - `gitdir`: absolute path of the workspace's `.git` file.
  - `commondir`: `../..`.
  - `HEAD`: output of `jj -R <ws> --ignore-working-copy log -r @- --no-graph -T commit_id`. If `@` is a non-empty described commit, run `jj new` first, but only in your own workspace; in another session's, ask that session.

  Then run `git -C <ws> read-tree HEAD`, or the worktree tracks nothing. Check `git -C <store> worktree list`, a non-zero `git -C <ws> ls-files | wc -l`, and no new sibling in `jj log -r '@ | @-'`.
- A lock, its reason string and a non-empty `@` each claim the tree is in use. Find the session that holds it (`skill://session-attribution`) and message it. Delete or overwrite the tree only after that session confirms its work is pushed or merged, and list in your report each tree you could not confirm. Do not `jj new` over another session's `@`, because that moves its work out from under it. Never force another session's lock (`-f -f`).
- Never run `git init` in a shared root such as `/tmp`, `/var/tmp`, `/mnt/nvme/sami/tmp` or a session-tmp root. git looks upward for a repository, so a stray `.git` there captures every git command run under it in a directory without its own. On 2026-11-01 an empty `/tmp/.git` made `git apply` in a `mktemp -d` directory skip every patch and still exit 0, so a test failed only on this box. A script that runs git in a temp directory sets `GIT_CEILING_DIRECTORIES="$(dirname "$PWD")"` and checks `git rev-parse --git-dir` before it relies on the answer. An untraced stray `.git` is renamed aside (any name other than `.git`) and recorded, not deleted.
- **`git worktree repair` cannot bring back a worktree whose admin entry was DELETED, which is the case a prune leaves.** Repair relinks an entry that still exists under a changed path; with `.git/worktrees/<name>` gone there is nothing to relink. Measured on git 2.43.0 in a throwaway repo (2026-11-12): `git -C <repo> worktree repair <path>` answers `unable to locate repository; .git file does not reference a repository`, running it inside the checkout answers `not a git repository`, and `git status` stays dead. **Telling sessions to repair after a prune produces an error for every one of them** — it was broadcast to 84 sessions on 2026-11-12 before anyone tested it.
- **To decide an orphan whose entry is gone, read its tree into a throwaway index.** This touches nothing shared:

  ```
  i=$(mktemp -u); g="git --git-dir=<repo>/.git --work-tree=<orphan>"
  GIT_INDEX_FILE=$i $g read-tree <commit>
  GIT_INDEX_FILE=$i $g update-index -q --refresh
  GIT_INDEX_FILE=$i $g diff-files --name-only
  GIT_INDEX_FILE=$i $g ls-files --others --exclude-standard
  ```


  **NEVER `export GIT_INDEX_FILE`.** Set it per command, as above. An agent shell is one
  persistent shell, so an exported value silently applies to every later git command in the
  session, in every repository: `git status` then reports nearly every tracked file as a
  staged deletion, and a commit made in that state commits those deletions. Measured
  2026-11-12: a recipe beginning with that export went to 84 sessions and contaminated at
  least one shell. It applies equally to any shell where someone exported it by hand, for
  instance to build a tree with `commit-tree`. To clear and check one: `unset
  GIT_INDEX_FILE`, then `git log --stat` over every commit the session made and look for a
  file count far larger than the change described. `git status` alone does not find a bad
  commit that was already made.

  Both lists empty and `<commit>` on a remote means the orphan holds nothing unsaved: remove it and write one ledger line. **Find `<commit>` by the worktree's own name first** (`git for-each-ref | grep <name>`), then confirm with `git ls-remote`. **A non-empty result is NOT evidence of lost work unless the base is the commit that tree was actually at**: measured the same night, one tree read 159 modified against a later bookmark and another read 50,861 modified and 255 untracked against `origin/main` while being clean at its own head `f3937ac0`. **Nobody deletes on a non-empty result, and nobody reports one as unsaved work either.**
- **Orphans are the normal state here, not an incident, so expect them in bulk.** Measured on sami-devbox 2026-11-12 05:05Z: **455 orphaned checkouts against 894 live, 299.9 GiB across the 392 that were sized.** Age is a usable first filter because live work is young: live worktrees ran a median of 6.6 days old with a **maximum of 8.4** (n=131), while 407 of the orphans were older than 7 days. **An orphan older than every live worktree is still not proof** — it is the cheap sort before the read-tree test.
- **`rm -rf` can fail silently on a tree you own, and the failure looks like someone else's files.** Review and receipt directories are often written read-only (`-r--r--r--` inside `dr-xr-xr-x`), and `rm` cannot unlink from a directory it cannot write, so it exits having deleted most of the tree and left a few hundred MB. **`chmod -R u+w <tree>` first, then `rm -rf`; no sudo is involved** — the files are already yours. Measured 2026-11-12: three directories reported `Permission denied` and stayed at 246 MB each until the chmod, then went instantly.
- **Measure a deletion with `du` on the paths you deleted, never with a `df` delta.** On a shared box other sessions are freeing and consuming space in the same window, and `df` cannot attribute any of it. On 2026-11-12 a `df` delta reported **58.8 GiB freed in a pass where three of the largest target directories were still on disk.**
