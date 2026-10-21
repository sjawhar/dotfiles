---
name: thermonuclear
description: "Use when asked for a thermonuclear review or a double review. Also use when a branch, PR or diff needs an uncompromising, diff-scoped bug/security audit and maintainability audit together."
disable-model-invocation: true
---

# Thermonuclear Review

Complements `ce-code-review`.

1. Set the scope from the request: the PR, the current branch, or the changed files that apply.
2. Collect the diff and the file excerpts the reviewers need, so that they do not guess.
3. On omp, start `thermonuclear-deep-review` (correctness, security) and `thermonuclear-code-quality` (maintainability) in one `task` call, as two entries in `tasks[]`.
4. On Claude Code, dispatch two independent read-only subagents in parallel, with the same names and prompts. If named agents are not available, load the two skills of the same names as separate review passes.
5. Give each reviewer the same scoped diff and context. Ask for prioritized findings, each with a file reference and evidence.
6. When both finish, synthesize:
   - Findings first. Merge duplicates across the reviewers.
   - Give more weight to a finding that both reviewers report.
   - Where the reviewers disagree, decide with your own judgment.
   - Keep summaries brief.
   - If the user already sees each reviewer's summary, do not restate them in full. Report the unified verdict, the highest-signal findings, and what stays uncertain.
