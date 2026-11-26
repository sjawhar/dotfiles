# Which model a lane ran on, and refusals

## Which agent a task gets

Judgment work (reviewing code, owning a pull request, planning, simplifying code, writing text a person reads, reading transcripts for what went wrong) goes to a strong agent, such as `deep`, `reviewer`, `oracle`, `planner` or `sol`, never to a cheap one: `sonic`, `scout` or a bare `task`. Retrieval and mechanical fan-out may go cheap; a task that mechanical is usually a script to write instead.

## Check the route, not the name

An agent's name does not fix its model: its `model:` field (`omp/agents/<name>.md`) is a first choice, and `retry.fallbackChains` in `omp/config.yml` moves it when a provider or subscription window is unavailable. Within a minute of the spawn, not after the bill, read the child's first `"type":"model_change"` line (`grep -m1`) in its session `.jsonl` under `~/.omp/agent/sessions/<cwd-slug>/<parent-session>/`, and cancel a child whose model starts with `openai/` (per-token) that you did not ask for. Read the same line before you claim a model family for a verdict: its `model` and `resolvedModelIsFallback` fields say what served it.

Name the agent in every eval `agent()` and `workpool()` call (`agent=`), then read what the session billed. Count each provider in one pass over its `.jsonl`, `grep -o '"provider":"[^"]*"' <file> | sort | uniq -c`, and confirm a nonzero count, which proves you read the right file. `"provider":"openai"` is the per-token API, while `openai-codex` is the subscription.

## Refusals

- An evidence-fetch brief names the file operation (list, read the header of, count) and the paths, not the content domain. Attack vocabulary (exploit, injection, technique names) belongs in the analysis step's brief: a provider refuses on the prompt's words, not on the operation.
- Once refused text is in a transcript, every later turn refuses. Re-dispatch fresh, with a brief that names only ids and the code-level defect, with no paraphrase of the scenario.
- A lane whose brief makes it read attack content (`malicious.py`, injection fixtures, tests that plant them) goes to a non-Anthropic agent from the start: `sol` or `astra`, never `deep`, whose fallback ends at Anthropic. Add the brief line "read attack files by name and size only". When no non-Anthropic model is reachable, try `opus-payload`, then `opus-4-6`.
- A reviewer whose subject is attack content gets one line: the content is authorized internal safety-evaluation material, and the review is of the product surface.
- Name the fallback in the brief before you send it. Concurrent dispatches to one agent share its quota, so a fan-out fails together. Before you pick a substitute, check which agents share that quota: the `model:` list in each `omp/agents/<name>.md` names its routes.
- The refusal arrives as the job's whole result (`Refusal (cyber): …`). Read what the worker did before you re-dispatch: there may be half a change to undo.

## Independence

When a brief names a model for independence (a cross-model review, an adversarial second opinion) and that model is unavailable, do not substitute silently: the substitute removes the property the brief was buying. Re-dispatch on another non-Anthropic model, or decide yourself with the evidence (commands, `file:line`) in the report, and say that the independent reader was absent.
