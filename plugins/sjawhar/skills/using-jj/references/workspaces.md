# Workspaces

## Where and how to add

- `scripts/agent-workspace new <repo> <name>` does the whole add for you, on a colocated store or a plain git one, and `agent-workspace rm <repo> <name>` forgets it again; the rest of this section is what it does and what to check when you add one by hand.
- Work in a named workspace, not the shared checkout. The store is normally `~/src/<repo>`. Outside an agent box (the Sysbox container `scripts/agentbox` runs one agent session in), put the workspace at `~/.worktrees/<repo>/<name>`. Inside a box, use its host-backed `~/boxes/<box>/<name>`. Read the box's mount contract: a path outside the mount list exists only in that box and dies with it.
- Pass the store explicitly. Without `-R` the add uses whatever store the current directory resolves to, which can be retired:
  `jj -R "$HOME/src/<repo>" workspace add "$HOME/.worktrees/<repo>/<name>" --name <name>`
- Then check the files are populated and inspect the new `.jj/repo` and `.git` pointers.
- Do not make a workspace only for a read-only review. Do not clone a shared checkout: the clone can keep the store as its remote and push stray refs into it.
- In a colocated repository `gh` works, but use jj. Git operations can desync jj.

## Colocation and LFS

- Plain `jj workspace add` colocates (`git.colocate` is true by default. Jj prints `Created Git worktree for the new workspace.`). The new workspace colocates only if the workspace you run the add in has a valid `.git`.
- `~/.dotfiles/.jjconfig.toml`, which every machine that uses these dotfiles loads, sets the LFS filter `required = true`. A `--no-colocate` add, or one where colocation is off, then aborts partway (`Failed to call the lfs filter`), and subprocess `git` exits 128 there.
- The unblock (`--config git.filter.drivers.lfs.required=false --config fsmonitor.backend=none` on every jj command, absolute paths) exits 0 but leaves LFS files as pointer text that tools read silently. Prefer the colocated add.
- Settings by source: `jj config list --include-defaults -T 'source ++ "|" ++ name'`.

## Worktree registration

- In an agent box, another box's bare `git worktree prune` deletes an unlocked worktree entry. Git then fails with `fatal: not a git repository`. `shims/jj` locks the entry `jj workspace add` creates, and `shims/git` locks `git worktree add` and refuses a bare prune. Check the lock. After the shims install, an old shell needs `hash -r`.
- Where the lock is missing, the named exception is `git -C <store> worktree lock <path> --reason '<why>'`.
- To diagnose, read the workspace's `.git`. No file: not colocated, so run git and `gh` from a colocated checkout. Its jj state is not lost. A `gitdir:` naming a missing directory: rebuild it with `disk-hygiene`'s recipe, then lock it.
- Never match `jj workspace list` against `ls <store>/.git/worktrees/`. Git names entries by basename plus a collision number.
- In `jj workspace list`, most rows have no path and the rest are relative. Match the name as `^<name>:`.

## Register-then-populate (only a shared or another session's checkout available)

1. `jj --ignore-working-copy -R <repo> workspace add <path> --name <n> -r main@origin`. Expect exit 1 (`This command must be able to update the working copy`) after it registers the name. Do not add it again.
2. Inside `<path>`, run `jj new main@origin`. It checks out main there and touches only that workspace. Sibling workspaces keep their commit IDs across both steps.

## `--ignore-working-copy` on an add by mistake

- It leaves a workspace with only `.git` and `.jj` (or, on some builds, an empty `@` on the root commit). A plain retry fails with `Workspace named '<name>' already exists`.
- Recover: check the source checkout is clean with `git -C <repo> --no-optional-locks status --porcelain` (deleting the flag snapshots it). Run `jj --ignore-working-copy -R <repo> workspace forget <name>`, `rm -rf` the literal path, and add again without the flag.

## Saving work

- A file copied into a new workspace is only on disk until a jj command there snapshots it. Put it in a described commit at once and check with `jj log -r @` that `@` is not empty.
- A file that reached the operation log can be read with `jj --ignore-working-copy --at-op <op> file show -r <commit> 'root-file:"<path>"'`. That is an old snapshot. Restoring it can revert later fixes.
- Snapshot and push early. Retire only your own superseded workspace with `jj workspace forget` (it prints `Removed Git worktree`). Stock jj 0.45.1's forget runs a repository-wide prune, fixed from `0.45.1-sami.20260909-184010`. Check `jj --version` before you forget on a shared store.

## Stale working copies

- `jj workspace update-stale` loses nothing. It snapshots on-disk edits into the old working-copy commit before it merges operations, then checks out the commit the repository selects. If the old operation is gone, it makes a recovery commit.
- It can delete an edited file from the directory without discarding it. Before you run it, record `jj --ignore-working-copy log -r @ --no-graph -T change_id`. After it, inspect `jj log -r 'change_id(<saved-id>)' -p` and `jj log -r @`.
- A nonempty divergent sibling can hold the edits. Address it by commit ID and run `jj restore --from <commit> <paths>` in your own workspace for the complete saved path set, or `jj edit` it. `removed N files` is a checkout, not data loss. Never treat an empty sibling as disposable before you check owner, bookmarks and descendants.

## Served checkouts

- A served checkout is a checkout a running service or another session reads files from. Advance one only when its working-copy change is empty. Fetch, then inspect it.
- Leave another session's edits in place and coordinate. Do not park them with `jj new`. If an operation already parked them, inspect the old working-copy commit and recover its whole path set with its owner. After `jj new`, `@-` is the new parent, not the parked edits.
- Recover the complete path set, lockfiles and generated files too. Use structured path output, not diff text split on spaces.
