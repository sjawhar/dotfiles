---
description: "Watch CI status, fix failures, and merge when green"
---

Watch the CI status for the current branch's PR. When checks fail, fix them. When everything is green, merge.

> **jj workspace note:** You may be in a non-default jj workspace with no `.git` directory. If `gh` commands fail, set `GIT_DIR` to point to the default workspace: `GIT_DIR=/path/to/default/.git gh ...`

**1. Find the PR:**
```bash
gh pr view --json number,headRefName,statusCheckRollup,mergeable
```

**2. Monitor CI status:**
- Check `statusCheckRollup` for the state of all checks
- If checks are still running, wait **60 seconds** and re-check
- Report which checks are pending/passing/failing
- If `mergeable` is false, rebase is needed (go to step 5)

**3. When checks fail:**
- Identify which check(s) failed from `statusCheckRollup`
- Get the run ID: `gh run list --branch <branch> --status failure --json databaseId -q '.[0].databaseId'`
- Fetch the logs: `gh run view <run-id> --log-failed`
- Analyze the failure and fix the issue
- Push the fix with `jj git push`
- Increment the failure counter **for this specific check**
- If the same check has failed **3 times**, stop and ask for help
- Return to step 2

**4. When all checks pass:**
- Verify the PR is mergeable
- Merge with: `gh pr merge --squash --delete-branch`
- Report success

**5. Handle main branch changes:**
- Run `jj git fetch` to get latest main
- If your branch needs rebasing (behind main):
  - Rebase with `jj rebase -d main`
  - Resolve any merge conflicts (preserving functionality from both sides)
  - Push with `jj git push`
  - Return to step 2

**6. Handle other edge cases:**
- If PR requires reviews and none are approved, report review status and wait
- If PR has merge conflicts that can't be resolved, stop and ask for help

**Done when:** PR is merged, or you encounter an unresolvable issue.

Keep iterating until done. If any command fails unexpectedly, report the error.
