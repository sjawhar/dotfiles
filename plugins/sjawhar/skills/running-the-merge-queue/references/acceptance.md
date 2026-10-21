# Pre-merge acceptance and its oracle

Read for any change to what executes: infrastructure, task data, agent or harness instructions, registry selection, CI orchestration. The repository's workflow defines its production-like surface. You verify the owner's proof and commission its oracle. You never implement or run it.

## Evidence rules

- A build, unit suite, import or low-level probe is groundwork, not the scenario. A green pipeline does not prove it scheduled the changed path.
- An unrun scenario is a `BLOCKED` row, never a missing row. Production observation comes after the proof, not instead of it.
- Keep a waiver's stated scope: PR, revision, scenario, duration. Never extend it to another blocker or PR. Never reject an actual authorized exception on principle.
- Put the observed version beside each live-behavior claim. "Adding X made it work" is correlation, not mechanism. Never relay an inferred mechanism as a constraint on other owners.

## Ask the runtime that executes the change

| Change | Evidence that covers it |
|---|---|
| Third-party agent, collector or scheduler config | That runtime consuming the config and giving the result. A helper reading the file is not the runtime |
| Node image or userData | A gating workload on a post-apply node with the new runtime. A green run all on old nodes proves no replacement behavior. A node probe proves only what it exercised |
| Registry-selected package | The entries supported launchers request, resolving and executing in the intended runner. It can change future launches with no redeploy. Migrate supported callers. An import or "every launch tracks main" proves no availability. Require no obsolete alias without a compatibility contract |
| Tightened acceptance assertions | A run on real data before merge. A safe theory does not prove valid real runs pass |
| Advisory lane | Which scenario ran and what it showed. The label neither grants nor denies evidence. Do not make it a required gate to accept a valid observation |
| Watcher offered as a merge condition | Attached run or resource identity, a jobs or state snapshot, a later observation. A first log line proves only that the process exists. An empty selection query can keep a watcher alive forever |

## Independent oracle

- Give the read-only `oracle` task the READY packet and exact head, and the PR file list with the body's verification section. Add the acceptance plan and artifacts, known gaps, and any scoped waiver with its source.
- Ask whether the evidence proves the changed behavior through the surface that executes it, at the intended revision. Require each unproven claim and gap named with a disposition. Relay them as the oracle's findings, with no code-review findings of yours.
- A reusable verdict is on the PR and names this head, or a shared tree with both SHAs. It comes from a session independent of the author's lane and covers the evidence now offered. An owner's "oracle done" is not one. Do not duplicate a qualifying verdict. A new run, head or evidence outside its scope needs new coverage.
- When the material makes a seat refuse, use an approved seat that does not, and keep independence. Seat rules are in SKILL.md step 5.

## Order and later changes

- Acceptance and the post-acceptance independent FINAL review stay separate from simplify and the pair. Acceptance on simplify's final code head runs before the pair: a claim of preserved behavior is not its execution.
- If the head moves later, use [head changes](head-changes.md). Never relabel the proof silently.
