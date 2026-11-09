---
name: analyze
description: Use before you report work done, checked or handled, or commit or open a PR; not another's diff. Use it at the analyze step of opening-a-pr, when you finished a task and the working copy or your unpushed commits hold your own changes, or when someone asks you to analyze changes you just made.
---

# Analyze

1. **Find the working-copy changes** with `git status --porcelain --untracked-files=all` (staged, unstaged and untracked files) and `git diff HEAD` (staged and unstaged changes). If there are none, report that and ask what to analyze.
2. **Run these agents in parallel** in one `task` call:
   - `type-checker`, `bug-finder` and `code-simplifier` have edit tools. Tell each: "Find issues and fix them directly in the files."
   - `code-reviewer` has no edit tools. Tell it: "Find issues and report each one with its file and line. Do not edit files."
   - Add `test-analyzer` only if the changes have test files. It also has no edit tools, so tell it to report.
3. **Grade each finding** after all agents finish, because they keep editing the same files until then:
   - **Critical**, must fix before shipping: bugs, security issues, type errors that cause runtime failures.
   - **High**, should fix: subtle bugs, missing edge cases, unsafe type casts.
   - **Medium**, consider fixing: type improvements, simplifications that make the code clearer.
   - **Low**, optional: style, minor suggestions.

   Fix every Critical and High finding (`opening-a-pr` requires it); Medium and Low are your call. Fix a one-file finding yourself; dispatch a `deep` agent when the fix spans more than one file.
4. **Check the ask, not only the diff, before you call anything done.** Re-read what was asked, item by item, and look at the result its asker will see — the rendered page, the command's output, the live record — because a fix applied is not a fix working, and an item you cannot show that way is reported as not done. For a claim this diff does not contain (a test, a build, a deploy, a subagent's report), run its command now: `skill://verification-before-completion`.
5. **Summarize** all findings by severity: file and line, a short description, the fix, and who applied it. Add a code example when the fix is longer than one line.
