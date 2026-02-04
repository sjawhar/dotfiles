---
description: "Fetch PR review comments and create action plan"
args: "[pr_number_or_url]"
---

Fetch all review comments on a pull request and create an action plan.

> **jj workspace note:** You may be in a non-default jj workspace with no `.git` directory. If `gh` commands fail, set `GIT_DIR` to point to the default workspace: `GIT_DIR=/path/to/default/.git gh pr view ...`

**1. Find the PR:**
- If argument provided, use that PR number/URL
- Otherwise, find the PR for the current branch: `gh pr view`
- If no PR found, report and stop

**2. Fetch all comments:**
- Get repo info: `gh repo view --json nameWithOwner -q .nameWithOwner`
- Get PR comments: `gh pr view <number> --comments`
- Get line-level review comments: `gh api repos/{owner}/{repo}/pulls/{number}/comments`
- Include both human reviews and bot reviews (Copilot, etc.)

**3. Categorize the feedback:**
- **Critical:** Bugs, security issues, broken functionality
- **High:** Code quality issues, unclear code, missing tests
- **Medium:** Style suggestions, alternative approaches
- **Low:** Nice-to-haves, minor improvements
- **Questions:** Items needing clarification
- **Resolved:** Threads marked as resolved (verify they're actually addressed)

**4. Verify each comment against the code:**
- Read the referenced code before accepting any feedback
- Confirm the comment still applies (code may have changed since the review)
- Understand the full context—reviewers may have missed surrounding code
- Note if any feedback is based on misunderstanding the implementation

**5. Handle conflicting feedback:**
- If reviewers gave conflicting feedback:
  - Summarize both positions
  - Recommend a resolution based on code context
  - Flag for user decision if unclear

**6. Create action plan:**
- List each item with its location (file:line)
- Group by file for efficient fixing
- Note any items that need discussion vs. can be fixed immediately

**7. Ready to execute:**
- Present the categorized action plan
- Ask the user: "Should I start addressing this feedback?"
- If yes, work through critical items first, then high, then medium
- If no, the plan is ready for later
