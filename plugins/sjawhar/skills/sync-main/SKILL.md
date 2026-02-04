---
description: "Fetch latest and rebase onto main"
---

Sync the current workspace with the latest main branch.

**1. Check workspace health:**
```bash
jj workspace update-stale  # safe to run even if not stale
```

**2. Fetch and rebase:**
```bash
jj git fetch && jj rebase -d main
```

If fetch fails (network, auth), report the error and stop.

**3. If there are merge conflicts:**
1. Run `jj log` to see divergent commits and understand what changed
2. For each conflicted file:
   - Read the conflict markers
   - Understand what each side changed
   - Resolution strategy:
     - **Different parts of file:** keep both changes
     - **Overlapping changes:** combine logically, preserving intent of both
     - **True conflict:** prefer our changes unless upstream clearly fixes a bug
3. Run `jj status` to confirm no conflicts remain

**4. Summarize:**
- Run `jj log -r 'main@origin..main' --limit 10` to see new commits from main
- Briefly list what came in (titles only)
- Note any that might affect your current work

**Done when:** Workspace is rebased onto main with no conflicts.
