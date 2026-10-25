# Which model a lane actually ran on, and what to do when one refuses

## Which tier a lane gets

The floor is by task class, not by role name. Reviewing code, owning a PR (fork or not), planning, simplifying code, writing or rewriting something a human will read, and reading transcripts for what went wrong never go to a low- or mid-tier model. Retrieval, mechanical fan-out and line-finding stay cheap — and a role that mechanical is usually a script you should write instead. Under `sdd` the role mapping already fixes the tier; this is the same floor for every other dispatch.

## Check the provider, not the name

The agent's name is what you reason about; the provider is what bills, and a named agent has been billed to a different provider than its name implies. Name the agent in every eval `agent()` and `workpool()` call, then read what it billed: first run `grep -c '"provider":"anthropic"'` on the session's `*.jsonl` and confirm a nonzero count, which proves the path is right, then grep for `"provider":"openai"` — that is the per-token API, while `openai-codex` is the subscription. Spend on `astra-api` or `gpt-api` that your workflow calls for needs no approval; an unintended route to them is what this check catches.

Naming the agent does not guarantee the route, so check within a minute of the spawn rather than after the bill: `grep -m1 '"type":"model_change"'` in the child's session `.jsonl` under `~/.omp/agent/sessions/<cwd-slug>/<parent-session>/`, and cancel anything whose model starts with `openai/` that you did not ask for. A spend alert only reports after the fact. Read the same line before claiming a model family for any verdict.

## Refusals

- An evidence-fetch brief describes the file operation, not the content domain. A worker sent to list, read the header of or count production eval logs gets those verbs and the paths; the attack vocabulary (cybertask, exploit, injection, technique names) belongs in the analysis step's brief. The provider refuses on the prompt's words, not on the operation.
- The same refusal fires on content a worker reads, and once refused text is in the transcript every later wake refuses: the session is wedged, not the step. Re-dispatch fresh with a brief naming only ids and the code-level defect, with no paraphrase of the scenario.
- A lane whose brief makes it read task attack content (`malicious.py`, injection fixtures, tests that plant them) goes to a non-Anthropic model from the start — `astra`, or `gpt-api` when astra reports `usage_limit_reached` — with the brief line "read attack files by name and size only". A reviewer whose subject is attack content gets one line saying the content is authorized internal safety-evaluation material and the review is of the product surface.
- `deep` is not a non-Anthropic route for refused work: its fallback chain ends at Anthropic (`~/.dotfiles/omp/config.yml`).
- Name the fallback in the brief before you send it: `astra`'s quota is shared across concurrent dispatches, so a fan-out dies together rather than one at a time. Check which agents share a quota before picking a substitute.
- The refusal arrives as the job's whole result (`Refusal (cyber): …`), so read what the worker actually did before re-dispatching: there may be nothing to undo, or half a change.

## Independence

When a brief names a model for independence — a cross-model review, an adversarial second opinion, a deliberately different reader — and that model is unavailable, never substitute silently: the substitution removes exactly the property the brief was buying. Either re-dispatch on another non-Anthropic model, or proceed on your own judgement with the evidence (commands, `file:line`) in the report itself, and say in the report that the independent reader was absent.
