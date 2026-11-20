---
name: skill-evals
description: "Use when changing skills, agent instructions, advisor prompts, or harness behavior and needing evidence that the change helps, before or after adopting it."
disable-model-invocation: true
---

# Skill evals

- Pick the route below by what the change changes.
- Freeze the cases, variants and grading before any model call: results must not steer them.
- A provider or grader error is not a score: a zero penalizes the wrong arm.
- Report n and the 95% interval, not only the delta.
- Do not tune a variant on the cases you report: it then wins only those.
- A case where the model only says which tool calls it would make measures stated intent, not behavior. It does not decide adoption.

## Routes

- Harness behavior you can switch per session (an extension feature, a context line, an advisor): measure it on historical sessions, not live ones, so keep every committed gate in `~/.dotfiles/omp/extensions/experiments/gates.json` off `random`. The `reflect` skill's experiment readout compares feature on versus off.
- Skill or instruction text: use the draft in your next matching task and fix each step it gets wrong. To show a behavior change, run three arms in fresh sessions: the old text, the new text, and a control with no skill text, without which you cannot tell a rule from what the model does anyway (`writing-agent-skills` has the commands and scoring). Or compare the failure type's rate per 100 records in `reflect`'s `counts.json` before and after.
- Agreement with Sami's own labels (a gate or classifier predicting his decision): re-run the stored population with its `score.py` in the private `sjawhar/agent-eval-data` (`experiments/askgate/`, `experiments/forkgate/`, `experiments/sami-proxy/eval/`). Report agreement with an interval, from a score that uses no LLM judge.

## Scenario runs

No shared scenario runner exists. The first change that needs one builds it in the repo whose prompt it tests, so it gates that repo's pull requests: `omp --mode rpc` with the production prompt, tools that record each Dispatch, Envoy and Legion call instead of making it, repeated trials, and a control run on the unchanged prompt.

The archived `agent-evals` runs, cases and comparison code (`src/agent_evals/comparison.py`, a cluster bootstrap over case families) are in `sjawhar/agent-eval-data`. Reproduce an archived comparison with `uv run --project <clone> agent-evals compare --run-dir <run>`.
