---
name: skill-evals
description: "Use when changing skills, agent instructions, advisor prompts, or harness behavior and needing evidence that the change helps, before or after adopting it."
---

# Skill evals

Measure an agent-facing change by the route that matches what it changes. Freeze cases,
variants and grading before any model call. A provider or grader error is never a score.
Report n and the interval, not only the delta.

| Change | Route | Evidence |
|---|---|---|
| Harness behavior that can be switched per session (an extension feature, a context line, an advisor) | A gate in the omp experiments extension (`omp/extensions/experiments/gates.json`, set to `random`) | The weekly `reflect` run's `experiments-readout.py`: per session, the share of Sami's turns that correct the agent, the share of merged PRs marked rework and spend per merged PR, feature on versus off with a 95% interval. A week of fleet traffic detects only large effects. |
| Skill or instruction text | A real-omp scenario run in the repo that owns the text, or a live behavior count | A real `omp` session given the text acts correctly on a fixed scenario, beside a control run with the text removed; or the target failure class's rate in the `reflect` skill's step-2 labels before and after the text landed. |
| Agreement with Sami's own labels (an ask gate, a proxy, a classifier) | Re-run the stored population in the private `sjawhar/agent-eval-data` repository (`experiments/askgate`, `experiments/forkgate`, `experiments/sami-proxy`, each with its `score.py`) | Agreement with his labels, with an interval, from a score that does not depend on an LLM judge. |

No shared scenario runner exists yet. The first change that needs one builds it in the
repo whose prompt it tests, so it can gate that repo's pull requests: `omp --mode rpc` with
the production prompt, recorder tools for Dispatch, Envoy and Legion calls, repeated
trials, and the control run.

A case where the model only describes the tool calls it would make measures stated intent,
not behavior; it does not decide adoption. Do not tune a variant on the cases you report:
hold some out.

The archived `agent-evals` runs, cases and comparison code (`agent_evals/comparison.py`, a
cluster bootstrap over case families) stay in `sjawhar/agent-eval-data`. Reproduce an
archived comparison with `uv run --project <clone> agent-evals compare --run-dir <run>`.
