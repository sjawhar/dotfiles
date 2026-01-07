## Version Control (jj)

This user uses [jj (Jujutsu)](https://github.com/martinvonz/jj) with a squash workflow:

- All changes accumulate in a single working copy change
- Don't create new commits for fixes—just make changes and re-push
- Don't re-describe commits when pushing
- Push with `jj git push`. Add `--named=<bookmark_name>=@` to create a new branch from the current commit.

### jj Commands (use these instead of git)

| Task | Command |
|------|---------|
| Status | `jj status` |
| Log | `jj log` |
| Diff | `jj diff` |
| Show current change | `jj log -r @` |
| List bookmarks | `jj bookmark list` |
| Push | `jj git push` |
| Pull/fetch | `jj git fetch` |
| Resolve conflicts | Edit files, conflicts auto-resolve on save |
| Update stale workspace | `jj workspace update-stale` |

### Workspaces

You may be in a **jj workspace** (not the default workspace). Check with `jj workspace list`.

In non-default workspaces:
- There is **no `.git` folder** - git commands will fail
- Always use `jj` commands, never `git`
- If the workspace is stale, run `jj workspace update-stale`
- If your commit was split, use `jj squash` to recombine

### Bookmarks and Remote Branches

- When a remote branch is deleted (e.g., after PR merge), the local bookmark tracking it is automatically deleted
- No need to run `jj bookmark forget` manually for tracked bookmarks
- Untracked local bookmarks must be deleted manually if desired
