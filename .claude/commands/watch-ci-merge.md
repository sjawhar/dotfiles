---
description: "Watch CI status, fix failures, and merge when green"
---

Watch the CI status for the current branch's PR. When checks fail, fix them. When everything is green, merge.

Steps:

1. **Find the PR:**
   ```bash
   gh pr view --json number,headRefName,statusCheckRollup,mergeable
   ```

2. **Monitor CI status:**
   - Check `statusCheckRollup` for the state of all checks
   - If checks are still running, wait and re-check periodically
   - Report which checks are pending/passing/failing

3. **When checks fail:**
   - Identify which check(s) failed
   - Fetch the logs: `gh run view <run-id> --log-failed`
   - Analyze the failure and fix the issue
   - Push the fix with `jj git push`
   - Return to step 2

4. **When all checks pass:**
   - Verify the PR is mergeable
   - Merge with: `gh pr merge --squash --delete-branch`
   - Report success

5. **Handle edge cases:**
   - If PR has merge conflicts, report and ask for guidance
   - If a check keeps failing after 3 attempts, stop and ask for help
   - If PR requires reviews, report the review status

Keep iterating until the PR is merged or you encounter an unresolvable issue.
