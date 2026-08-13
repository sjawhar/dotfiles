---
name: fork-maintenance
description: "Use when working on a forked repository that uses an octopus merge (sami) to combine feature branches. This skill covers triaging where changes belong in a fork (which branch? new branch? upstream PR?), managing octopus merge parents (add, remove, rebase), resolving multi-parent merge conflicts, syncing with upstream, and verifying work is ready to ship. Load this skill whenever you need to decide where a fix or feature should go in a fork repo, manage the octopus merge structure, check if fork work is complete (am I done?, what is left to ship?), or follow the contribution lifecycle (push, CI, reviews, deploy). Also triggers on: sami, octopus merge, fork triage, which branch does this go on, rebase fork, existing issues, check CI."
---

# Fork Maintenance

This user maintains forks using an **octopus merge pattern**: a merge commit (typically called `sami`) that combines independent feature/fix branches as parents. Each fork also has a `ci` branch for build workflows. This pattern is used across multiple repos, not just one.

## The Pattern

```
sami (octopus merge, N parents)
├── feat/voice-mode          branch off common-base
├── feat/web-perf-optimize   branch off common-base
├── fix/session-blank-render branch off common-base
├── ci                       build workflow
└── ... more feature/fix branches
```

- **`sami`** is the integration point. It has no code changes of its own — just combines parents.
- **Feature/fix branches** are parents of sami, never children. All development happens on these branches.
- **`ci`** branch holds CI/build workflow changes.
- **`common-base`** is the commit most branches share as their parent (often the last upstream release or a known-good upstream commit). Using the same base avoids conflicts when combining into sami.

## Decision: Where Does This Change Go?

When you need to make a change, the first question is always: where does it belong?

```
Is this a bug in upstream code (identical in dev)?
├── YES → New fix/ branch off common-base → add as sami parent
└── NO → Is this a bug in one of our branches?
    ├── YES → Fix on that branch (child commit, move bookmark)
    └── NO → Is this a new feature?
        ├── YES → New feat/ branch off common-base → add as sami parent
        └── NO → Is this a CI/build change?
            └── YES → Fix on the ci branch
```

### Checking if a Bug is Upstream

```bash
# Compare our code with upstream dev
git show dev:path/to/file.tsx | sed -n 'START,ENDp'  # upstream
jj file show sami:path/to/file.tsx | sed -n 'START,ENDp'  # ours

# If identical → upstream bug, new branch off common-base
# If different → our bug, fix on the branch that changed it
```

### Finding Which Branch Changed a File

```bash
# Check each sami parent for changes to a specific file
for parent in $(jj log --no-graph -T 'change_id.shortest(8) ++ "\n"' -r 'parents(sami)'); do
  changed=$(jj diff --stat -r "$parent" 2>/dev/null | grep "path/to/file" | wc -l)
  if [ "$changed" -gt 0 ]; then
    desc=$(jj log --no-graph -T 'description.first_line()' -r "$parent")
    echo "$parent: $desc"
  fi
done
```

## Creating a New Branch

```bash
# Find common-base (the parent most branches share)
# Look at existing branches to identify it:
jj log --no-graph -T 'change_id.shortest(4) ++ " " ++ description.first_line() ++ "\n"' \
  -r 'parents(parents(sami))' | sort | uniq -c | sort -rn | head -5

# Create branch off common-base
jj new <common-base> -m "fix(scope): description"

# Make changes, typecheck
# ...

# Set bookmark
jj bookmark set fix/my-fix
```

## Adding a Branch to Sami

```bash
# Method 1: Using revset (clean, handles divergent changes)
jj rebase -s sami -d "all:sami- ~ <old_parent_if_replacing>" -d <new_branch>

# Method 2: Using commit IDs (when divergent changes cause issues)
PARENTS=$(jj log --no-graph -T 'parents.map(|p| "-o " ++ p.commit_id().short(12)).join(" ")' -r sami)
NEW=$(jj log --no-graph -T 'commit_id.short(12)' -r <new_branch>)
eval "jj rebase -r sami $PARENTS -o $NEW"

# Verify conflict-free
jj log --no-graph -T 'conflict' -r sami  # Should print: false
```

## Removing a Branch from Sami

When a branch is merged upstream or no longer needed:

```bash
jj rebase -s sami -d "all:sami- ~ <branch_to_remove>"
```

## Modifying an Existing Branch

To add fixes to a branch that's already a sami parent:

```bash
# Create child of the branch
jj new feat/voice-mode -m "fix(app): additional fix"
# Make changes...
# Move bookmark to new tip
jj bookmark set feat/voice-mode
# Sami auto-rebases since its parent changed
# Verify: jj log --no-graph -T 'conflict' -r sami
```

## Syncing with Upstream

```bash
jj git fetch

# Rebase all feature branches onto new upstream:
# For each branch, rebase onto the new common-base
jj rebase -b <branch> -d <new-common-base>

# OR if you just want to update sami's view of upstream:
# Fetch and verify no new conflicts appeared in sami
jj git fetch && jj log --no-graph -T 'conflict' -r sami
```

## Conflict Resolution in Octopus Merges

When sami has conflicts after adding/removing parents or syncing:

1. **Check what's conflicted:**
   ```bash
   jj new sami  # Create child to work in
   jj status    # See conflicted files
   ```

2. **Understand the conflict:** Octopus merge conflicts often have more than 2 sides. Read all conflict markers carefully.

3. **Resolve:** Load the `resolve-conflicts` skill for detailed conflict resolution strategy. The key insight for octopus merges: conflicts are usually from lockfiles or version bumps, not logic conflicts. For lockfiles, regenerate rather than merge.

4. **Squash resolution into sami:**
   ```bash
   jj squash  # Moves resolution from @ into sami (parent)
   ```

## Build & Deploy (Web Frontend)

If the fork includes a web frontend:

```bash
deploy-web          # Pull latest CI build from gh-pages
deploy-web --local  # Build from current sami checkout
```

**After ANY web UI code change, deploy.** Code changes in jj branches are invisible until built and deployed. This is the #1 source of "it doesn't work" reports.

### Manual Build

```bash
jj new sami                          # Checkout sami merge
cd packages/app && bun run build     # Build
# Verify your code is in the bundle:
grep "unique_string_from_your_change" dist/assets/*.js
# Deploy:
rm -rf ~/opencode-web-frontend && cp -r dist ~/opencode-web-frontend
caddy reload --config ~/.dotfiles/opencode-web/Caddyfile
# Smoke test:
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/
```

## Orientation

Run `jj-agent-status` to see the repo state: current position, active agents, branches, divergent/conflicted changes, and what needs attention.

Ship via the `opening-a-pr` skill.

## Common Pitfalls

- **Basing a branch on `dev@upstream` instead of common-base** → lockfile conflicts in sami. Always use the same base as existing branches.
- **Committing on sami instead of a branch** → can't be merged upstream, hard to isolate. Always work on named branches.
- **Forgetting to deploy** → code changes are invisible in the web app. Always `deploy-web` after frontend changes.
- **Leaving changes as sami children** → they should be on a named branch that's a sami parent. Rebase onto the right branch.
- **Using `jj rebase` with change IDs when divergent changes exist** → use commit IDs instead, or the `all:sami-` revset pattern.
- **Cross-branch conflicts aren't inherited** — resolving conflicts in individual parent branches does NOT resolve the octopus merge. Sibling branches can conflict with each other in ways not visible in either parent. Verify the merge separately.
- **Never typecheck a conflicted working copy** — conflict markers are syntax errors. Typecheck per-branch after resolving each one, not on the merge with markers.
- **Never hand-merge lockfiles** — `bun.lock`, `package-lock.json`, `yarn.lock` are generated. Delete and run the package manager to regenerate.
- **Never edit a conflicted jj change directly** — use `jj new <conflicted>`, resolve in the child, then `jj squash` back. This preserves change identity.
- **Verify files exist before investigating** — if you're debugging a feature, check the files exist in your current checkout first. They may only be in the merge or a specific branch.
- **jj conflict format**: `+++++++` = snapshot (full file state), `%%%%%%%` = diff (branch's changes to apply). For semantic merges, preserve the diff side's intent.
- **"Done" means deployed** — code changes in jj branches are invisible until built and deployed. For web UI changes, run `deploy-web` and verify your code is in the bundle.
