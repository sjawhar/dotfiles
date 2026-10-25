---
name: skill-evals
description: "Use when changing skills, agent instructions, advisor prompts, or harness behavior and needing evidence that the change helps, before or after adopting it."
---

# Skill evals

- Pick the route below by what the change changes.
- Freeze the cases, variants and grading before any model call.
- A provider or grader error is never a score.
- Report n and the interval, not only the delta.
- Hold out cases: never tune a variant on the cases you report.
- A case where the model only describes the tool calls it would make measures stated intent, not behavior. It never decides adoption.

| Change | Route and evidence |
|---|---|
| Harness behavior you can switch per session (an extension feature, a context line, an advisor) | Set a gate to `random` in `omp/extensions/experiments/gates.json`. The weekly `reflect` run's `experiments-readout.py` reports feature on versus off with a 95% interval. It reports, per session, the share of Sami's turns that correct the agent, the share of merged PRs marked rework, and spend per merged PR. A week of fleet traffic detects only large effects. |
| Skill or instruction text | Use the draft on real work and fix what it misses. To show a behavior change, follow the testing section of `writing-agent-skills` (fresh sessions, old text against new text plus a no-guidance control, 3 to 6 runs per arm, cases from real sessions), or compare the failure class's rate in the `reflect` step-2 labels before and after. |
| Agreement with Sami's own labels (an ask gate, a proxy, a classifier) | Re-run the stored population with its `score.py`, in the private `sjawhar/agent-eval-data` repo: `experiments/askgate`, `experiments/forkgate` or `experiments/sami-proxy`. Report agreement with an interval, from a score that does not use an LLM judge. |

No shared scenario runner exists. The first change that needs one builds it in the repo whose prompt it tests, so that it gates that repo's pull requests. It needs `omp --mode rpc` with the production prompt, recorder tools for Dispatch, Envoy and Legion calls, repeated trials, and the control run.

The archived `agent-evals` runs, cases and comparison code (`agent_evals/comparison.py`, a cluster bootstrap over case families) are in `sjawhar/agent-eval-data`. To reproduce an archived comparison: `uv run --project <clone> agent-evals compare --run-dir <run>`.
