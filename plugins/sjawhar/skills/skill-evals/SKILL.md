---
name: skill-evals
description: "Use when changing skills, agent instructions, advisor prompts, or harness behavior and needing evidence that the change helps, before or after adopting it."
---

# Skill evals

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

- Pick the route below by what the change changes.
- Freeze the cases, variants and grading before any model call: results must not steer them.
- A provider or grader error is never a score: a zero penalizes the wrong arm.
- Report n and the 95% interval, not only the delta.
- Never tune a variant on the cases you report: it then wins only those.
- A case where the model only says which tool calls it would make measures stated intent, not behavior. It never decides adoption.

| Change | Route and evidence |
|---|---|
| Harness behavior you can switch per session (an extension feature, a context line, an advisor) | Set a gate to `random` in `~/.dotfiles/omp/extensions/experiments/gates.json`. The weekly `reflect` run's `experiments-readout.py` reports feature on versus off with a 95% interval. It reports, per session, the share of Sami's turns that correct the agent, the share of merged PRs the delivery dashboard marks as rework, and spend per merged PR. A week of fleet traffic detects only large effects; report the interval, not "no effect". |
| Skill or instruction text | Use the draft in your next matching task; fix each step it gets wrong. To show a behavior change, run three arms in fresh sessions, the old text, the new text and a control with no skill text, because without the control you cannot tell a rule from what the model does anyway; commands and scoring are in "Test it" of `skill://writing-agent-skills`. Or compare the failure class's rate in `reflect` step 2's labels before and after. |
| Agreement with Sami's own labels (a gate or classifier predicting his decision) | Re-run the stored population with its `score.py`, in private `sjawhar/agent-eval-data`: `experiments/{askgate,forkgate,sami-proxy}`. Report agreement with an interval, from a score that does not use an LLM judge. |

No shared scenario runner exists. The first change that needs one builds it in the repo whose prompt it tests, so that it gates that repo's pull requests. It needs `omp --mode rpc` with the production prompt, tools that record each Dispatch, Envoy and Legion call instead of making it, repeated trials, and a control run on the unchanged prompt.

The archived `agent-evals` runs, cases and comparison code (`src/agent_evals/comparison.py`, a cluster bootstrap over case families) are in `sjawhar/agent-eval-data`. To reproduce an archived comparison: `uv run --project <clone> agent-evals compare --run-dir <run>`.
