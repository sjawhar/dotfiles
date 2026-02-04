---
description: "Push changes and open a PR"
---

Push my current changes and create a pull request.

> **jj workspace note:** You may be in a non-default jj workspace with no `.git` directory. If `gh` commands fail, set `GIT_DIR` to point to the default workspace: `GIT_DIR=/path/to/default/.git gh ...`

**1. Review current state:**
- Run `jj status` to see working copy changes
- Run `jj log -r @` to see the current commit description
- Run `jj diff` to review what will be pushed
- If there are no changes, report that and stop

**2. Check for existing PR:**
- Run `jj bookmark list` to see if there's already a bookmark
- If a bookmark exists, check if a PR exists: `gh pr view 2>/dev/null`
- If PR exists, skip to pushing updates

**3. Push to remote:**
- If there's no bookmark: derive a branch name from the commit description (e.g., `fix-typo-in-readme`, `add-user-auth`)
  - Push with `jj git push --named=<name>=@`
- If there's already a bookmark: push with `jj git push`

**4. Create or update Pull Request:**
- If PR already exists: report the PR URL and note that changes were pushed
- If no PR exists: create with `gh pr create`
  - Title: concise summary of the change
  - Body: brief description of what changed and why
  - Include `Closes #N` if this addresses a known issue

**Done when:** Changes are pushed and PR exists (new or updated).

If any command fails, report the error and stop.
