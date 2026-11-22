---
name: thermonuclear
description: "Use when asked for a thermonuclear review or a double review. Also use when a branch, PR or diff needs an uncompromising, diff-scoped bug/security audit and maintainability audit together."
disable-model-invocation: true
---

# Thermonuclear Review

This is the double review. `ce-code-review` is the general diff or PR review.

1. **Set the scope** from the request: the PR, the current branch, else the files changed against the base branch.
2. **Collect the diff,** plus the function holding each changed hunk and the definitions it calls, so the reviewers do not guess.
3. **Start the two reviewers on omp** in one `task` call: `thermonuclear-deep-review` (correctness, security) and `thermonuclear-code-quality` (maintainability), as two entries in `tasks[]`.
   - omp is oh-my-pi, the agent harness. Its shell has `OMPCODE=1` set. Its `task` tool takes a `tasks[]` list.
4. **Dispatch the same two on Claude Code** as independent read-only subagents in parallel, each reading its rubric from `https://raw.githubusercontent.com/sjawhar/legion/main/skills/<name>/SKILL.md`.
   - **A failed read stops that reviewer.** Report the URL and the error.
5. **Give both the same scoped diff and context.**
   - Ask both for findings ranked Critical, High, Medium or Low, each with a file reference and evidence.
   - Tell each to keep scratch in its own `d=$(mktemp -d)`.
   - Tell each to delete only `"$d"`, never a glob such as `<dir>/tmp.*`, which also matches other roles' live scratch.
6. **Report only these, in this order,** when both finish:
   1. The findings.
   2. The verdict: merge, or fix first.
   3. What neither reviewer settled:
      - each point one reviewer left open (a claim it says it could not check, a question it asks) that the other did not close;
      - each part of the scope a reviewer says it did not read.

   - Merge duplicates.
   - Order the findings by severity, Critical first.
   - Within one severity, list first each finding both reviewers raised, marked "both reviewers": independent agreement.
   - Where they disagree, decide and say why.
   - Do not restate a reviewer's summary.
