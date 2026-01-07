---
description: "Push changes and open a PR"
---

Push my current changes and create a pull request.

**1. Review current state:**
- Run `jj status` to see current changes
- Run `jj log -r @` to see the current commit description
- Run `jj diff` to review what will be pushed

**2. Push to remote:**
- If there's no bookmark: choose a reasonable branch name and push with `jj git push --named=<name>=@`
- If there's already a bookmark: push with `jj git push`

**3. Create Pull Request:**
- Use `gh pr create` with:
  - A clear title summarizing the changes
  - A description with: summary of what changed and why, any testing done, notes for reviewers

If there are any issues at any step, stop and report them.
