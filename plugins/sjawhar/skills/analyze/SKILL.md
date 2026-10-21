---
name: analyze
description: Use when code changes in the working copy need a quality pass before a commit or a PR. Also use when asked to analyze recent changes.
---

# Analyze

1. **Find the working-copy changes** with `git diff` and `git diff --name-only`. If there are none, report that and ask what to analyze.
2. **Run these agents in parallel** in one `task` call:
   - `type-checker`, `bug-finder` and `code-simplifier` have edit tools. Tell each: "Find issues and fix them directly in the files."
   - `code-reviewer` has no edit tools. Tell it: "Find issues and report each one with its file and line. Do not edit files."
   - Add `test-analyzer` only if the diff has test files. It also has no edit tools, so tell it to report.
3. **Fix the reported findings** after all agents finish. Fix a small finding yourself. Dispatch a `deep` agent for a finding that needs a larger change.
4. **Summarize** all findings. Put each finding in a severity:
   - **Critical**, must fix before shipping: bugs, security issues, type errors that cause runtime failures.
   - **High**, should fix: subtle bugs, missing edge cases, unsafe type casts.
   - **Medium**, consider fixing: type improvements, simplifications that make the code clearer.
   - **Low**, optional: style, minor suggestions.

   For each finding, give the file and line, a short description, the fix, and whether you or an agent applied it. Add a code example where it helps.

**Done when** the summary shows all findings by severity.
