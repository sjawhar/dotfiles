---
name: analyze
description: Use when code changes in the working copy need a quality pass before a commit or a PR. Also use when asked to analyze recent changes.
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

4. **Summarize** all findings by severity: file and line, a short description, the fix, and who applied it. Add a code example when the fix is longer than one line.
