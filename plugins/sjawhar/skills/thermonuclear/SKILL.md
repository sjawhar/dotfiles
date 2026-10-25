---
name: thermonuclear
description: "Use when asked for a thermonuclear review or a double review. Also use when a branch, PR or diff needs an uncompromising, diff-scoped bug/security audit and maintainability audit together."
disable-model-invocation: true
---

# Thermonuclear Review

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

`ce-code-review` is the general diff or PR review; this is the double review.

1. Set the scope from the request: the PR, the current branch, else the files changed against the base branch.
2. Collect the diff, plus the function holding each changed hunk and the definitions it calls, so they do not guess.
3. On omp, start `thermonuclear-deep-review` (correctness, security) and `thermonuclear-code-quality` (maintainability) in one `task` call, as two entries in `tasks[]`.
4. On Claude Code, dispatch the same two as independent read-only subagents in parallel. Each reads its rubric from Legion: `https://raw.githubusercontent.com/sjawhar/legion/main/skills/<name>/SKILL.md`. A failed read stops that reviewer: report the URL and the error.
5. Give both the same scoped diff and context. Ask for findings ranked Critical, High, Medium or Low, each with a file reference and evidence.
6. When both finish, report:
   - Findings first. Merge duplicates.
   - List a finding both reviewers report first, marked "both reviewers": independent agreement.
   - Where they disagree, decide and say why.
   - Do not restate a reviewer's summary. Report only the verdict (merge, or fix first), the findings in severity order, and what neither reviewer settled.
