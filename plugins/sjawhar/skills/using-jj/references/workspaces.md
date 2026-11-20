# Workspaces

## Add one

- `scripts/agent-workspace new <repo> <name>` makes a workspace of `~/src/<repo>` under the machine's workspaces root; in an agent box it uses one of the box's `--dir` directories and refuses when there is none. `agent-workspace rm <repo> <name>` forgets it and deletes the directory. `--help` has the options.
- By hand, put `<path>` where the script would: `<root>/<repo>/<name>`. Outside an agent box (the container `scripts/agentbox` runs one agent session in), `<root>` is `/mnt/nvme/<user>/worktrees` on a machine with that disk, else `~/.worktrees`. In a box, use a `worktrees` directory under one of the host directories that `AGENTBOX_DIRS` lists: the box's `$HOME`, its `/tmp` and every other path on the box's own disk die with the box.
- Pass the store: without `-R` the add uses whatever store the current directory resolves to. `jj -R ~/src/<repo> workspace add <path> --name <name>`. Then check that the files are there (`ls <path>`), and that the new `<path>/.jj/repo` and `<path>/.git` pointers both point into that store.
- Keep the default colocated add; jj then prints `Created Git worktree for the new workspace.` An add colocates only when the workspace you run it in is colocated, with a valid `.git`.
- `.jjconfig.toml` makes the LFS filter required, so in a repository with LFS files a `--no-colocate` add, or any add that does not colocate, aborts halfway (`Failed to call the lfs filter`) and leaves a 0-byte file; `git` run in that workspace exits 128.
- The unblock, `--config git.filter.drivers.lfs.required=false --config fsmonitor.backend=none` on every jj command with absolute paths, exits 0 but leaves LFS files as pointer text that tools read without an error. Prefer the colocated add.
- To see which file sets each setting: `jj config list --include-defaults -T 'source ++ "|" ++ name ++ "\n"'`.
- Do not make a workspace only for a read-only review, and do not clone a shared checkout: the clone keeps the store as its remote and can push stray refs into it.
- Match a workspace by name (`^<name>:` in `jj workspace list`), not by path: the path column is relative to where you run it. Git names `<store>/.git/worktrees/` entries by basename plus a number, so they do not match workspace names either.

## Add from a checkout that is not yours

1. `jj --ignore-working-copy -R <repo> workspace add <path> --name <name> -r main@origin`. It registers the workspace, then exits 1 (`This command must be able to update the working copy`) with only `.git` and `.jj` in `<path>`. Do not add it again: that fails with `Workspace named '<name>' already exists`.
2. In `<path>`, run `jj new main@origin`. It checks out main there and touches no other workspace.

An add that got `--ignore-working-copy` by mistake is in the same state; finish it with step 2. To start over instead, check that the source checkout is clean (`git -C <repo> --no-optional-locks status --porcelain` prints nothing; an add without the flag snapshots it), run `jj --ignore-working-copy -R <repo> workspace forget <name>`, `rm -rf` the literal `<path>`, and add again without the flag.

## Worktree registration

- In an agent box, another box's bare `git worktree prune` deletes an unlocked worktree entry, because from inside a box every other box's worktree looks missing; git then fails with `fatal: not a git repository`. `shims/jj` locks the entry `jj workspace add` creates, and `shims/git` locks each `git worktree add` and refuses a bare prune. Check the lock: `git -C <store> worktree list --porcelain` prints `locked` under the entry. After the shims install, run `hash -r` in an older shell.
- Where the lock is missing, run `git -C <store> worktree lock <path> --reason '<why>'`, a named exception to using jj.
- If git says `fatal: not a git repository` in a workspace, read its `.git`. No file: the workspace is not colocated; run git and `gh` from a colocated checkout (its jj state is intact). A `gitdir:` naming a missing directory: rebuild the entry with the `disk-hygiene` skill's recipe, then `git -C <store> worktree lock <path> --reason '<why>'`.

## Save and retire

- Put a file you copied into a workspace in a described commit at once, and check with `jj log -r @` that `@` is not empty.
- A file no jj command has snapshotted is not stored. Find saved states with `jj op log --limit <n>` and `jj --at-op <op> file list`, then recover with `jj restore --from <commit> <paths>`.
- A file from an old operation can be read with `jj --at-op <op> file show -r <commit> 'root-file:"<path>"'`. It is an old snapshot: restoring it can revert later fixes.
- Snapshot and push work as soon as it is worth keeping: a lost or deleted workspace takes unpushed work with it.
- Retire only your own superseded workspace, by name: `jj workspace forget <name>`, `agent-workspace rm`, or `knives finish` in a fork. Never delete your current directory.
- Before `jj workspace forget` on a shared store, run `jj --version`: a stock jj 0.45.1 forget runs a repository-wide prune, which the `-sami` builds from `0.45.1-sami.20260909-184010` do not.

## Stale working copies

- `jj workspace update-stale` loses nothing: it snapshots on-disk edits into the old working-copy commit, then checks out the commit the repository selects.
- It can remove an edited file from the directory without discarding it. Before it, record `jj --ignore-working-copy log -r @ --no-graph -T change_id`. After it, read `jj log -r 'change_id(<saved-id>)' -p` and `jj log -r @`.
- If the edits are in a divergent sibling, address it by commit ID and `jj edit` it, or `jj restore --from <commit> <paths>` with the complete path set. `removed N files` is a checkout, not data loss. Check owner, bookmarks and descendants before you treat an empty sibling as disposable.

## Served checkouts

- A served checkout is one that a running service or other sessions read files from, such as `~/.dotfiles`. Advance it only when its working-copy change is empty: fetch, then inspect it (`jj status`, `jj log -r '@ | @- | main@origin'`) before you move it. For `~/.dotfiles`, the root `AGENTS.md` names the commands.
- Leave another session's edits in place and coordinate with it. Do not move the working copy off them with `jj new`: the edits stay in the old working-copy commit. If that happened, inspect that commit and recover its complete path set, lockfiles and generated files included, with its owner. After `jj new <rev>`, `@-` is `<rev>`, not the commit that holds the edits. Use structured path output, not diff text split on spaces.
