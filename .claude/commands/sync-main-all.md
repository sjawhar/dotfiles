---
description: "Sync all workspaces: rebase onto main, resolve conflicts, skip active ones"
---

Sync all jj workspaces in the project with the latest main branch.

**CRITICAL: Some workspaces may have active Claude sessions working in them. Do not interfere with those.**

## Step 1: Fetch latest and list workspaces

```bash
jj git fetch
jj workspace list
```

Note the current directory so you can return to it later.

## Step 2: Identify active vs inactive workspaces

For each workspace, check if it appears to be actively in use:

```bash
# Check for Claude processes in the workspace
pgrep -f "claude.*<workspace_path>" || echo "No Claude process found"

# Check for recent file modifications (last 5 minutes)
find <workspace_path> -type f \( -name "*.ts" -o -name "*.py" -o -name "*.rs" \) -mmin -5 2>/dev/null | head -5
```

If either check suggests activity, **skip this workspace**.
If uncertain, **leave it alone** — safer to skip than to interfere.

## Step 3: For each INACTIVE workspace

Navigate to the workspace and perform the sync:

```bash
cd <workspace_path>
jj workspace update-stale  # if needed
jj log -r 'ancestors(@, 10)'  # review recent history
```

**Before rebasing, check for work in progress:**
- Run `jj status` to see modified files in the working copy
- Run `jj log -r '::@ ~ ::main'` to see commits not yet on main
- If there are changes that could be lost, **stop and report** — do not proceed

**Rebase onto main:**
```bash
jj rebase -d main
```

**If there are merge conflicts:**
1. Check `jj log` to see what diverged
2. Review the content of conflicting commits — understand what each side changed
3. Resolve conflicts preserving ALL functionality from both sides
4. Never delete local changes without explicit permission
5. Run `jj status` to confirm no conflicts remain

## Step 4: Return and verify

Return to the original working directory.

Summarize:
- Which workspaces were synced successfully
- Which workspaces were skipped (and why — active agent, conflicts needing review, etc.)
- Any working copy changes or commits that need attention

**The goal:** Every workspace is either:
1. Clean and up-to-date with main, OR
2. Left untouched because an agent is actively working there

**Nothing should be lost.** If in doubt, skip the workspace and report.
