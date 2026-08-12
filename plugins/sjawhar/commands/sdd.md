---
name: sdd
description: Execute work via subagent-driven development with Sami's fixed agent mapping (deep implements, ultrabrain plans/reviews).
disable-model-invocation: true
---

# Subagent-Driven Development (Sami's mapping)

Load the `subagent-driven-development` skill and execute the given work through it with this fixed agent mapping:

- **Implementation and debugging** → `task(category="deep", ...)`
- **Planning and reviews** → `task(category="ultrabrain", ...)`
- **Plan and PR review** → oracle

You are the coordinator: dispatch, verify results file-by-file, integrate, and manage the backlog. **Do not plan or implement yourself** — burning your own context on implementation instead of orchestrating is the failure mode this command exists to prevent.

**Parallelize aggressively.** When the plan has independent tasks (different files, different modules, no sequential dependency), dispatch them to separate subagents in the same turn — don't drip-feed one at a time. Structure the plan itself to maximize independent tasks. When one lane blocks (a decision, a review in flight, an external event), keep every other lane moving; a blocked task never idles the whole pipeline.

Pipeline: plan (writing-plans skill, dispatched to `task(category="ultrabrain")`) → oracle reviews the plan → subagents implement → the `opening-a-pr` skill → the `landing-a-pr` skill. Every plan carries a `## Hardening ledger` section, empty at the start.

**The ledger is the implementer's contract, not a suggestion.** Every implementer prompt says: *take the shortcut if it gets the feature working, but log it in the hardening ledger the moment you take it, and return your ledger entries with your report.* You accumulate those entries across subagents and hand the whole list to `opening-a-pr`, which is where they get paid off — before Sami ever looks at the PR. An implementer who hid a hack instead of logging it has broken the contract; send it back.

**Envoy is the comms channel between you and your subagents.** Get your session ID from `envoy_whoami` before dispatching, and put two things in every subagent prompt: (1) your session ID with the instruction to message you via `envoy_send(target_session="<your-id>", ...)` when they finish, hit a blocker, or need a decision they cannot make themselves — including their own session ID (from `envoy_whoami`) so you can reply; (2) that a clarification question sent this way beats guessing. On your side: a subagent that looks stalled gets an `envoy_send` asking for status **before** you kill or re-dispatch it — it may be deep in legitimate work, and killing it burns everything it learned. Give it a reasonable window to answer; silence after that, or a reply confirming it is wedged, is when you re-dispatch.

All of these steps are pre-authorized; do not stop to ask permission between them.
