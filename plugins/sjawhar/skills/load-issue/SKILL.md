---
description: "Load a GitHub issue as the current plan"
args: "<issue_number_or_url>"
---

Load GitHub issue $ARGUMENTS as the current working plan.

> **jj workspace note:** You may be in a non-default jj workspace with no `.git` directory. If `gh` commands fail, set `GIT_DIR` to point to the default workspace: `GIT_DIR=/path/to/default/.git gh issue view ...`

**1. Sync to latest main:**
- Run `jj git fetch && jj rebase -d main`
- Skip only if user explicitly says "don't sync" or "stay on current branch"

**2. Fetch the issue:**
- If given a number: `gh issue view <number>`
- If given a URL: extract owner/repo/number and use `gh issue view -R <owner>/<repo> <number>`
- If the issue is not found, report the error and stop
- Read the full issue body, comments, and any linked PRs

**3. Analyze the issue:**
- Summarize the goal/problem statement
- List specific requirements or acceptance criteria
- Note any design decisions or constraints mentioned
- Identify any open questions that need clarification

**4. Verify issue validity against current code:**
- Issues may be stale or already resolved—check before proceeding
- Examine the relevant code to confirm the problem still exists
- If the issue appears outdated or already fixed:
  - Report findings to the user
  - Ask whether to close the issue or proceed anyway
- Look for any related changes that may have partially addressed the issue

**5. Set up context:**
- This issue is now your current task
- Reference it when making commits and PRs
- The PR should close this issue when merged

**6. Propose a plan:**
- Break down the issue into implementation steps
- Identify which files likely need changes
- Flag any ambiguities that need clarification before starting

**7. Wait for confirmation:**
- Present the plan and ask: "Ready to start implementation?"
- Do NOT begin coding until the user confirms

If the issue references other issues or PRs, briefly summarize how they relate.
