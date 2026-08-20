---
name: sdd
description: Execute work via subagent-driven development with Sami's fixed agent mapping (deep implements, ultrabrain plans/reviews).
disable-model-invocation: true
---

# Subagent-Driven Development (Sami's mapping)

Load the `subagent-driven-development` skill and execute the given work through it with this fixed agent mapping:

- **Implementation and debugging** → `task` with `agent: "deep"`
- **Planning and reviews** → `task` with `agent: "ultrabrain"`
- **Plan and PR review** → `task` with `agent: "oracle"` (read-only)

You are the coordinator: dispatch, verify results file-by-file, integrate, and manage the backlog. **Do not plan or implement yourself** — burning your own context on implementation instead of orchestrating is the failure mode this command exists to prevent.

**Parallelize aggressively.** Structure the plan itself to maximize independent tasks; dispatch independent tasks in the same turn; a blocked lane never idles the pipeline.

Pipeline: plan (writing-plans skill, dispatched to the `ultrabrain` agent) → oracle reviews the plan → subagents implement → the `opening-a-pr` skill → the `landing-a-pr` skill. Every plan carries a `## Hardening ledger` section, empty at the start.

**The ledger is the implementer's contract, not a suggestion.** Every implementer dispatch includes: *take the shortcut if it gets the feature working, but log it in the hardening ledger the moment you take it, and return your ledger entries with your report.* You accumulate the entries across subagents. When implementation is done, you — the coordinator — run the `opening-a-pr` skill yourself; at its empty-the-ledger step, dispatch each unpaid entry to `deep` as its own bounded fix task and fold the results in before the PR opens. An implementer who hid a hack instead of logging it has broken the contract; send that task back to `deep` with the gap named.

**Comms:** subagent results auto-deliver when the agent yields — never poll and never idle waiting on one lane while another is dispatchable. A subagent is stalled only when its lane blocks the pipeline and it has produced neither a result nor a question: send it one direct message (in omp: `hub` send to its roster id) stating exactly what you need; if that goes unanswered, cancel it and re-dispatch the task with the gap written into the new prompt. Pass large context by file path, never pasted into dispatch prompts. Load the `using-subagents` skill before your first dispatch for prompt structure.
