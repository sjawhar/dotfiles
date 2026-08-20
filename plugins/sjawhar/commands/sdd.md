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

Pipeline: plan (writing-plans skill, dispatched to `task(category="ultrabrain")`) → oracle reviews the plan → subagents implement → the `opening-a-pr` skill → the `landing-a-pr` skill. Every plan carries a `## Hardening ledger` section, empty at the start.

**The ledger is the implementer's contract, not a suggestion.** Every implementer prompt says: *take the shortcut if it gets the feature working, but log it in the hardening ledger the moment you take it, and return your ledger entries with your report.* You accumulate those entries across subagents and hand the whole list to `opening-a-pr`, which is where they get paid off — before Sami ever looks at the PR. An implementer who hid a hack instead of logging it has broken the contract; send it back.

**Comms:** load the `using-subagents` skill for dispatch-prompt and stall discipline. Coordination rides the harness's own channel — in omp that is `hub`: subagent results auto-deliver on completion, `hub` op:"list" names the live roster, and a stalled subagent gets a `hub` send (and a chance to answer) before any cancel or re-dispatch. No Envoy setup is needed for subagent comms.
