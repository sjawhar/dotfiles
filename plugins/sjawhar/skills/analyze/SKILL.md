---
name: analyze
description: Use when you are about to commit or open a PR on code you wrote, at the analyze step of opening-a-pr, when you finish a task and the working copy or your unpushed commits hold your own changes, or when asked to analyze changes you just made. Not for reviewing someone else's diff.
---

# Analyze

1. Find the working-copy changes with `git status --porcelain --untracked-files=all` (staged, unstaged and
   untracked files) and `git diff HEAD` (staged and unstaged changes), and your unpushed commits with
   `git log @{upstream}..HEAD` and `git diff @{upstream}...HEAD` (with no upstream, diff against the base branch).
   If there are none, report that and ask what to analyze.
2. Run these agents in parallel in one `task` call:
   - `type-checker`, `bug-finder` and `code-simplifier` have edit tools. Tell each: "Find issues and fix them
     directly in the files."
   - `code-reviewer` has no edit tools. Tell it: "Find issues and report each one with its file and line. Do not
     edit files."
   - Add `test-analyzer` only if the changes have test files. It has no edit tools either, so tell it to report.
3. Grade each finding after all agents finish, since they edit the same files until then:
   - Critical, must fix before shipping: bugs on the normal path the change serves, security issues, type errors
     that cause runtime failures.
   - High, should fix: subtle bugs (bugs that show only on an error path, under a race, or on rare input), missing
     edge cases, unsafe type casts.
   - Medium, consider fixing: type improvements, simplifications that make the code clearer.
   - Low, optional: style, minor suggestions.
4. Fix every Critical and High finding (`opening-a-pr` requires it); Medium and Low are your call. Fix a one-file
   finding yourself. When the fix spans more than one file, dispatch a `deep` agent (`task` with `agent: "deep"`,
   the implementation agent, which has edit tools) and give it each finding with its file, line and fix.
5. Summarize all findings by severity: file and line, a short description, the fix, and who applied it. Add a code
   example when the fix is longer than one line.
