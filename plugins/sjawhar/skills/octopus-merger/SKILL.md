---
description: "Continuously resolve conflicts in an octopus merge as parents rebase"
---

You are managing an octopus merge commit. Your commit merges multiple parent commits, and those parents may be rebased independently, causing conflicts to appear in your merge.

Your job is to continuously monitor and resolve conflicts:

1. **Initial state check:**
   - Run `jj log -r 'parents(@)'` to identify the merge parents (note their change IDs)
   - Run `jj log -r 'ancestors(@, 2)'` to see your commit and its immediate parents
   - Run `jj status` to check for current conflicts

2. **Watch for workspace staleness:**
   - Run `jj workspace update-stale` to sync with any rebased commits
   - This may cause your commit to be split if changes occurred

3. **If commit was split (jj reports this):**
   - Run `jj log -r @` to find your current position
   - You may need to squash changes back together: `jj squash --from <split-commit> --into @`
   - Or abandon orphaned empty commits: `jj abandon <empty-commit>`

4. **Resolve any conflicts:**
   - For each conflicted file, read and resolve the conflict markers
   - In octopus merges, there may be **more than two sides** — read carefully
   - Combine changes from all sides, preserving functionality from each
   - Ensure resolved code compiles/passes basic checks

5. **Loop:**
   - After resolving, wait **3 minutes**, then repeat from step 2
   - Check if all parents are now on main: `jj log -r 'parents(@) & ::main'`
   - Continue until all parent branches are merged to main
   - If **15 minutes** pass with no new conflicts or changes, stop watching

6. **When finished:**
   - If there's one remaining parent commit from main, squash your resolution into it: `jj squash --into <parent>`
   - Report which conflicts were resolved and final state

7. **Report status periodically:**
   - Which parents have been merged to main
   - Which conflicts were resolved
   - Current state of the merge commit

If you encounter issues you can't resolve (ambiguous conflicts, broken code after resolution), stop and ask for help.
