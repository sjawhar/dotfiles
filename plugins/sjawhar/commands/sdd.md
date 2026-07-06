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

Pipeline: plan (writing-plans skill) → oracle reviews the plan → subagents implement → one PR → oracle reviews the PR → all CI green. For the push/PR/CI steps, use the `push-pr` skill — it covers bookmark handling, PR creation, and watching CI (reproduce failures locally instead of iterating against CI). All of these steps are pre-authorized; do not stop to ask permission between them.
