# Workspaces

## Add one

- `scripts/agent-workspace new <repo> <name>` makes a workspace of `~/src/<repo>` under the machine's workspaces root; in an agent box it uses one of the box's `--dir` directories and refuses when there is none. `agent-workspace rm <repo> <name>` forgets it and deletes the directory. `--help` has the options.
- By hand, pass the store: without `-R` the add uses whatever store the current directory resolves to. `jj -R ~/src/<repo> workspace add <path> --name <name>`, then list the new directory.
- Keep the default colocated add. `.jjconfig.toml` makes the LFS filter required, so in a repository with LFS files a `--no-colocate` add aborts halfway (`Failed to call the lfs filter`) and leaves a 0-byte file.
- Do not make a workspace only for a read-only review, and do not clone a shared checkout: the clone keeps the store as its remote and can push stray refs into it.
- Match a workspace by name (`^<name>:` in `jj workspace list`), not by path: the path column is relative to where you run it. Git names `<store>/.git/worktrees/` entries by basename plus a number, so they do not match workspace names either.

## Add from a checkout that is not yours

1. `jj --ignore-working-copy -R <repo> workspace add <path> --name <name> -r main@origin`. It registers the workspace, then exits 1 (`This command must be able to update the working copy`) with only `.git` and `.jj` in `<path>`. Do not add it again: that fails with `Workspace named '<name>' already exists`.
2. In `<path>`, run `jj new main@origin`. It checks out main there and touches no other workspace.

An add that got `--ignore-working-copy` by mistake is in the same state; finish it with step 2.

## Worktree registration

- If git says `fatal: not a git repository` in a workspace, read its `.git`. No file: the workspace is not colocated; run git and `gh` from a colocated checkout (its jj state is intact). A `gitdir:` naming a missing directory: rebuild the entry with the `disk-hygiene` skill's recipe, then `git -C <store> worktree lock <path> --reason '<why>'`.

## Save and retire

- Put a file you copied into a workspace in a described commit at once, and check with `jj log -r @` that `@` is not empty.
- A file from an old operation can be read with `jj --at-op <op> file show -r <commit> 'root-file:"<path>"'`. It is an old snapshot: restoring it can revert later fixes.
- Retire only your own superseded workspace, by name: `jj workspace forget <name>`, `agent-workspace rm`, or `knives finish` in a fork. Never delete your current directory.

## Stale working copies

- `jj workspace update-stale` loses nothing: it snapshots on-disk edits into the old working-copy commit, then checks out the commit the repository selects.
- It can remove an edited file from the directory without discarding it. Before it, record `jj --ignore-working-copy log -r @ --no-graph -T change_id`. After it, read `jj log -r 'change_id(<saved-id>)' -p` and `jj log -r @`.
- If the edits are in a divergent sibling, address it by commit ID and `jj edit` it, or `jj restore --from <commit> <paths>` with the complete path set. `removed N files` is a checkout, not data loss. Check owner, bookmarks and descendants before you treat an empty sibling as disposable.

## Served checkouts

- A served checkout is one that a running service or other sessions read files from, such as `~/.dotfiles`. Advance it only when its working-copy change is empty; for `~/.dotfiles`, the root `AGENTS.md` names the commands.
- Do not move the working copy off another session's edits with `jj new`: the edits stay in the old working-copy commit. If that happened, recover its complete path set, lockfiles and generated files included, with its owner. Use structured path output, not diff text split on spaces.
