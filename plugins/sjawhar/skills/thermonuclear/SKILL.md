---
name: thermonuclear
description: "Use when asked for a thermonuclear review or a double review. Also use when a branch, PR or diff needs an uncompromising, diff-scoped bug/security audit and maintainability audit together."
disable-model-invocation: true
---

# Thermonuclear Review

`ce-code-review` is the general diff or PR review; this is the double review.

1. Set the scope from the request: the PR, the current branch, else the files changed against the base branch.
2. Collect the diff, plus the function holding each changed hunk and the definitions it calls, so the reviewers do not guess.
3. On omp, start `thermonuclear-deep-review` (correctness, security) and `thermonuclear-code-quality` (maintainability) in one `task` call, as two entries in `tasks[]`.
4. On Claude Code, dispatch the same two as independent read-only subagents in parallel, each reading its rubric from `https://raw.githubusercontent.com/sjawhar/legion/main/skills/<name>/SKILL.md`. A failed read stops that reviewer: report the URL and the error.
5. Give both the same scoped diff and context, and ask for findings ranked Critical, High, Medium or Low, each with a file reference and evidence. Tell each to keep scratch in its own `d=$(mktemp -d)` and delete only `"$d"`, never a glob such as `<dir>/tmp.*`, which also matches other roles' live scratch.
6. When both finish, report only the verdict (merge, or fix first), the findings in severity order, and what neither reviewer settled:
   - Merge duplicates. List first a finding both reviewers raised, marked "both reviewers": independent agreement.
   - Where they disagree, decide and say why.
   - Do not restate a reviewer's summary.
