# Working-copy identity and snapshots

Read from the matching step of the using-jj skill. Git commands here are read-only
diagnostics or warnings about what not to run; version-control changes use jj.

- **Auto-snapshot:** there is no staging; every `jj` command snapshots. `@` is the on-disk working-copy change; change IDs stay stable across rewrites, commit IDs do not.

- **Moving work to another workspace does not save it; a commit does.** A file copied into a fresh
  `jj workspace add` directory lives only on disk until a jj command there snapshots it, so the
  workspace can be registered, its `@` empty, and the content nowhere in the store. A workspace
  under a path outside the agent-box mount list exists only inside that box: from anywhere else the
  path does not exist, and `jj workspace list` shows the workspace registered with an empty `@`,
  which reads like lost work while the box still has the file. Nothing outside that box can see or
  recover it, and an unsnapshotted file dies with its box. A file that did reach the store's
  operation log is recoverable with
  `jj --ignore-working-copy --at-op <op> file show -r <commit> 'root-file:"<path>"'`, but that copy
  is a stale snapshot, not the current file; restoring it can silently revert later fixes. After
  moving anything out, put it in a described commit before doing anything else, and confirm with
  `jj log -r @` that `@` is not empty.

- **`--ignore-working-copy` makes a diff lie about your tree, because that is what it disables.**
  It is the right flag for reading another lane's store without snapshotting their working copy
  (`jj --ignore-working-copy bookmark list`, `log`, `workspace list`) and the wrong one for any
  question about what is on disk: after a tool rewrites a file,
  `jj --ignore-working-copy diff --stat` still reads `0 files changed`. When the question is "did
  my edit land", use plain `jj diff` and let the snapshot happen; keep the flag for someone else's
  repository, where snapshotting is the harm.
  **Never use it on a command that creates a working copy.** `jj workspace add` with
  `--ignore-working-copy` leaves the new workspace with zero files, because the flag disables the
  checkout the command exists to perform. On jj 0.45.1-sami it exits 1 with `This command must be
  able to update the working copy`, but only after creating the git worktree and registering the
  name, so the directory holds only `.git` and `.jj` and a plain retry fails with
  `Workspace named '<name>' already exists`; other builds have reported success on the root commit
  (`jj st`: `parent=000000000000 empty=true`). Recovery is the same either way. Confirm the source
  checkout is clean with read-only `git -C <repo> --no-optional-locks status --porcelain`, because
  dropping the flag snapshots it. Then run `jj --ignore-working-copy -R <repo> workspace forget
  <name>`, `rm -rf` the literal path, and add again without the flag. Applying the flag to every
  jj command produces an empty tree; the failure is silent in the command that causes it and loud
  only in whatever reads the tree next.
  **That failure is also the way in when the only source workspaces are the shared checkout or another lane's**, because it registers the name without snapshotting them: run `jj --ignore-working-copy -R <repo> workspace add <path> --name <n> -r main@origin`, accept the exit 1, then run `jj new main@origin` inside `<path>`, which checks out main there and touches only that workspace. On 0.45.1-sami, sibling workspaces keep their commit IDs across both steps.

- **CRITICAL — `jj new` comes BEFORE the work, never after:** auto-snapshot puts edits into
  whatever `@` is *now*. If the next piece of work deserves its own commit, run `jj new` first,
  then edit. Running `jj new -m "msg"` after editing creates an **empty** commit with your
  message and strands the work in the previous change, and there is no after-the-fact way to
  separate "my new edits" from "@'s prior content" short of path surgery. The switch-away form
  is the same trap wearing git's clothes: `jj new main@origin` with uncommitted edits does NOT
  carry them along the way `git checkout` would; they are already snapshotted into the commit
  you left, and the new `@` is empty. They look lost; they are not. Recovery:
  `jj log -r 'heads(@-::) | @-'` (or `jj op log`) to find the commit you left, then
  `jj restore --from <that-commit> <paths>` naming the paths, then compare the file list
  (`jj diff -r <that-commit> --stat` vs `jj diff --stat`) before committing; a partial restore
  looks exactly like a complete one until the diff says otherwise.
  **After a push, the working copy IS the published commit.** Keep editing and the next snapshot
  silently amends it: the branch moves non-fast-forward with nobody deciding to rewrite history,
  and a sibling based on the pushed commit is orphaned. `jj new` belongs in the same command as
  the push, never a later step. Diagnostic once suspected: a non-zero
  `git merge-base --is-ancestor <pushed-sha> <new-head>` proves the amend.
  **Repair, once it has happened and a reviewer's verdict names the pushed SHA:** put the bookmark
  back on that SHA with `jj bookmark set <name> -r <pushed-sha> --allow-backwards` (without the
  flag jj refuses: `Refusing to move bookmark backwards or sideways`), after which
  `jj git push -b <name> --dry-run` says `Bookmark <name>@origin already matches` and there is
  nothing to push. Carry the edit onto a fresh child with `jj new <name>` and
  `jj restore --from <amended-sha> <paths>`, then `jj abandon <amended-sha>`, which now deletes no
  bookmark because none points at it. Two repairs look right and are not (jj 0.45.1-sami):
  - `jj abandon <amended-sha>` while the bookmark still points at it **deletes the bookmark**
    (`Deleted bookmarks: <name>` and a hint about `--deleted`), and the next `jj git push -b <name>`
    is a delete push, `bookmark: <name> [delete from <sha>]`, which removes the branch on origin.
    GitHub then closes the pull request and may refuse `gh pr reopen`, so the content has to move
    to a new PR.
  - `jj abandon --retain-bookmarks` moves the bookmark to the *parent*, so the push becomes
    `move backward` onto trunk.

  Read the `Changes to push` line before any push after a repair: `delete from` and
  `move backward` are written in plain words, and `--dry-run` prints them without acting.
  **The rebase case, which "`jj new` before editing" does NOT prevent.** `jj rebase` moves the
  commits you named and leaves `@` exactly where it was. Rebase branch B onto A's tip while `@`
  sits on A's commit, then edit a file for B: the snapshot amends **A**, the approved head, and
  because B is now A's child, B carries the hunk at once, before any `jj new`; a `jj new B`
  afterwards puts the same on-disk file on a third commit. The rule is therefore **assert which
  commit `@` is on before editing**,
  `jj log -r @ --no-graph -T 'change_id.short() ++ " " ++ description.first_line()'`, after every
  rebase, edit or workspace update. Once a rewrite is suspected, grep the hunk's own identifier in
  the file at each SHA along the branch (pre-rebase, the approved tip, the current tip): absent,
  absent, present pins where the hunk entered the history. That count is occurrences within one
  file at one SHA and says nothing about how many commits carry the hunk. Diff from the SHA a gate
  actually cited, never from the branch name: "I only appended" can be true of intent and false of
  history.

- **CRITICAL — bare `jj describe` rewrites `@`'s existing message:** it does not "commit your
  work"; it renames whatever `@` already is. Before describing, check
  `jj log -r @ --no-graph -T 'description.first_line()'`; if `@` already carries a message that
  matters, you are about to destroy it. A `squash --into` can quietly move `@` back onto that
  commit. Recovery: `jj op log` shows the describe operation naming the old commit ID;
  `jj log -r <that-commit-id> --no-graph -T 'description'` still prints the pre-rewrite text.

- **CRITICAL — never path-extract from a merge commit:** `jj split <paths>` and
  `jj squash --from <merge> <paths>` do not move "the change to those paths"; a merge commit's
  path content *is* its merge resolution, so extraction deletes those files from the merged tree
  and leaves the parent unbuildable (whole files vanish, manifests revert to a parent's version).
  Work snapshotted into a merge stays there or is reconstructed as the intended delta on a
  fresh child. Inspect the saved merge when recovering; never use a repository-global
  `jj op restore` to repair a shared store.

- **Colocated repos look dirty to git:** jj parks git HEAD at `@`'s parent, so `@`'s content shows
  in `git status` as uncommitted changes. That is expected state, not a mess to clean up:
  `git reset --hard`, `git checkout -- .`, `git clean` or `git stash` there destroys `@`'s work,
  recoverable only up to the last jj snapshot. The same fact has a measurement consequence. While
  `@` *is* the bookmark commit, `HEAD` is the commit *before* it: `git show HEAD:<file>` does not
  contain a file the bookmark adds, and when the bookmark commit *edits* an existing file, it
  succeeds and returns the pre-edit version with exit 0. After `jj new` off the bookmark, `HEAD`
  equals it. In general `git show HEAD:` sees your top commit only while `@` is a *descendant* of
  it, so appending a second commit and staying on it leaves HEAD on the first. A gate reading one
  revision stale is *consistently* wrong, so it presents as "my fix doesn't work". `git rev-parse
  HEAD` and `jj log -r @-` agree whenever `@` has one parent; on a merge working copy `@-` is every
  parent and HEAD is the *first* only, so the question is whether `@-` is the commit you meant to
  test. `jj new`, `jj commit` and `jj squash` into the parent all leave the content in `@-` (HEAD
  sees it); `jj describe` on `@` does not, because the edits stay in `@`. "Commit first" therefore
  means one of the first three, never a describe. Name the SHA you measured, never `HEAD`;
  `jj new` after a push fixes this and the after-push amend trap at once.

- **After any interruption, check WHAT `@` is before trusting a grep of the working copy.** A stale
  working copy's grep output looks exactly like a grep of main, carries file and line numbers, and
  can be entirely wrong: `@` may be another lane's in-flight refactor rather than main. The control
  is one line, `jj log -r '@ | main@origin'`, and check that the two are related. `jj status` does
  not tell you this; it reports a clean working copy that can be many commits and a refactor away
  from main.

- **`git status` cannot certify a jj workspace clean.** In a colocated workspace jj syncs its working-copy commit into git's HEAD, so `git status --porcelain` reads empty while that HEAD sits on no branch and is pushed nowhere; the content is at risk and git says nothing. Use `jj st` and `jj log -r @` in that workspace.
