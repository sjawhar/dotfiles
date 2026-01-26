## Dotfiles Repo

The `~/.dotfiles` repo is a personal dotfiles repo. Push directly to main—no PRs needed.

## Working Style

### Planning

When creating plans:
- **Prepare for feedback** — treat plans as drafts to iterate on, not final deliverables
- **Front-load uncertainty** — call out areas where you're unsure or see multiple approaches
- **Show your reasoning** — explain why you chose an approach so the user can evaluate trade-offs
- **Don't declare plans "complete"** — say "ready for review" and expect revisions

### Code Patterns

Before implementing new functionality, search the codebase for similar patterns. Follow existing conventions by default.

If you see a cleaner alternative:
- Note it explicitly: "The existing pattern does X, but Y might be cleaner because..."
- **Don't deviate from existing patterns without explicit approval**
- Consistency with existing code takes priority unless the user agrees to change it

### Scope Awareness

- Implement exactly what's requested — don't add unrequested features
- If uncertain whether something is in scope, ask rather than assume it is
- When the user says something is "not in scope" or "not in the plan," respect that boundary

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

### Parallel Workspaces

Multiple jj workspaces may be active simultaneously (potentially with other Claude sessions).

- **Main branch moves frequently** — rebase often with `jj git fetch && jj rebase -d main`
- **Expect merge conflicts** — resolve without losing others' work
- **Verify your workspace** — confirm you're operating on the right directory before making changes

### Merge Conflict Resolution

When resolving conflicts after rebase:
1. **Check divergent commits first** — run `jj log` to see what diverged
2. **Never lose functionality** — review what changed in the commits being merged
3. **Don't delete local changes** without explicit permission

### Bookmarks and Remote Branches

- When a remote branch is deleted (e.g., after PR merge), the local bookmark tracking it is automatically deleted
- No need to run `jj bookmark forget` manually for tracked bookmarks
- Untracked local bookmarks must be deleted manually if desired
