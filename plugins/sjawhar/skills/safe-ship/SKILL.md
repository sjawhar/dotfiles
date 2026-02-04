---
description: "Full shipping workflow: analyze, review, CI, merge, and track deferred issues"
---

Complete the full safe shipping workflow for the current changes.

> **jj workspace note:** You may be in a non-default jj workspace with no `.git` directory. If `gh` commands fail, set `GIT_DIR` to point to the default workspace: `GIT_DIR=/path/to/default/.git gh ...`

**Prerequisites:** This workflow uses `/analyze`, `/push-pr`, `/code-review-jj:code-review`, and `/watch-ci-merge`. Ensure these are available.

## Phase 1: Pre-Push Analysis

1. Check for changes: `jj status` — if no changes, report and stop
2. **Run `/analyze`** to check type safety, bugs, and code simplification
3. **Address all critical and high severity issues** found
4. **Re-run `/analyze`** (max 3 iterations)
5. If critical/high issues persist after 3 rounds, stop and ask for guidance
6. Track any medium and low priority suggestions for later

## Phase 2: Push and Create PR

1. Run `/push-pr` to push changes and create a pull request
2. If PR already exists, just push the updated changes

## Phase 3: Code Review

1. **Run `/code-review-jj:code-review`** on the PR
2. Also fetch Copilot/bot review comments: `gh api repos/{owner}/{repo}/pulls/{number}/comments`
3. **Address all critical and high severity issues** from either source
4. **Re-run code review** (max 3 iterations)
5. If critical/high issues persist after 3 rounds, stop and ask for guidance
6. Track any medium and low priority suggestions for later

## Phase 4: CI and Merge

1. **Run `/watch-ci-merge`** to monitor CI, fix failures, and merge
2. This handles: CI monitoring, failure fixing, rebasing, and merging
3. If blocked, `/watch-ci-merge` will stop and report

## Phase 5: Track Deferred Issues

**Only if there are deferred items**, create a GitHub issue after merging:

```bash
gh issue create --title "Follow-up: [brief description] cleanup" --body "..."
```

The issue body should include:
- Source of each suggestion (analyze, code-reviewer, Copilot)
- Link to the original PR comment if applicable (e.g., `https://github.com/{owner}/{repo}/pull/{number}#discussion_r{id}`)
- File and line number where applicable
- The specific suggestion or improvement
- Priority level (medium/low)

---

**Important:**
- Do NOT skip the analyze loop — quality gates matter
- Do NOT merge with unaddressed critical/high issues
- DO track all deferred items — nothing should be forgotten
- If blocked at any phase, report progress and stop

**Done when:** PR is merged and any deferred issues are tracked.
