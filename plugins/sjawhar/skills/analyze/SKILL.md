---
name: analyze
description: Use when code changes in the working copy need a quality pass before a commit or a PR. Also use when asked to analyze recent changes.
---

# Analyze

1. **Find the working-copy changes** with `git diff` and `git diff --name-only`. If there are none, report that and ask what to analyze.
2. **Run these agents in parallel** with the `task` tool, in read-write mode: `type-checker`, `bug-finder`, `code-simplifier` and `code-reviewer`. Add `test-analyzer` only if the diff has test files. Tell each agent: "Find issues and fix them directly. Edit files to implement your suggested improvements."
3. **Summarize** after all agents finish. Put each finding in a severity:
   - **Critical**, must fix before shipping: bugs, security issues, type errors that cause runtime failures.
   - **High**, should fix: subtle bugs, missing edge cases, unsafe type casts.
   - **Medium**, consider fixing: type improvements, simplifications that make the code clearer.
   - **Low**, optional: style, minor suggestions.

   For each finding, give the file and line, a short description, and the suggested fix, with a code example where it helps.

**Done when** the summary shows all findings by severity.
