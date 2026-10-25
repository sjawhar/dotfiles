# Pre-merge acceptance and its oracle

Read for any change to what executes: infrastructure, task data, agent or harness instructions, registry selection, CI orchestration. The repository's workflow docs name which environment counts as production-like; use that one. You verify the owner's proof and commission its oracle. You never implement or run it.

## Evidence rules

- The developer owns the proof on a production-like surface, and its later production check. CI, unit tests, an internal helper or testing after merge do not replace it.
- Each required scenario row records the behavior or contract, the consuming component with its revision or config, and the environment. When the row claims two commits have the same tree, it names both commit SHAs and the tree hash. It records the entry point and exact command, run ID, outcome, duration and evidence link. Its state is one of:
  - `RAN`
  - `BLOCKED`, including never run. It stops readiness.
  - `WAIVED-BY-SAMI`: only Sami's explicit waiver of the named blocker, linked with its scope. It covers no other scenario.
- Return infrastructure, tooling and skill gaps to the owner to fix before merge. Never run their acceptance or invent a waiver.
- A build, unit suite, import or low-level probe is groundwork, not the scenario. A green pipeline does not prove it scheduled the changed path.
- An unrun scenario is a `BLOCKED` row, never a missing row. Production observation comes after the proof, not instead of it.
- Keep a waiver's stated scope: PR, revision, scenario, duration. Never extend it to another blocker or PR. When Sami's waiver covers the blocker, accept it; do not ask for the waived scenario anyway.
- Put the observed version beside each live-behavior claim. "Adding X made it work" is correlation, not mechanism. Never relay an inferred mechanism as a constraint on other owners.

## Ask the runtime that executes the change

| Change | Evidence that covers it |
|---|---|
| Third-party agent, collector or scheduler config | That runtime consuming the config and giving the result. A helper reading the file is not the runtime |
| Node image or userData | A gating workload on a post-apply node with the new runtime. A green run all on old nodes proves no replacement behavior. A node probe proves only what it exercised |
| Registry-selected package | The entries supported launchers request, resolving and executing in the intended runner. It can change future launches with no redeploy. Migrate supported callers. An import or "every launch tracks main" proves no availability. Do not ask the owner to keep an old alias unless a documented compatibility promise requires it |
| Tightened acceptance assertions | A run on real data before merge. A safe theory does not prove valid real runs pass |
| Advisory lane | Which scenario ran and what it showed. The label neither grants nor denies evidence. Accept a valid observation from an advisory lane as evidence; the lane does not need to become a required check first |
| Watcher offered as a merge condition | Attached run or resource identity, a jobs or state snapshot, a later observation. A first log line proves only that the process exists. An empty selection query can keep a watcher alive forever |

## Independent oracle

- Reuse a qualifying oracle verdict already posted on the PR. Otherwise run a read-only `oracle` task for the missing coverage only. Require a disposition for each oracle gap. A verdict that is claimed but not posted on the PR is not evidence: ask for it on the PR.
- Give the read-only `oracle` task the READY packet and exact head, and the PR file list with the body's verification section. Add the acceptance plan and artifacts, known gaps, and any scoped waiver with its source.
- Ask whether the evidence proves the changed behavior through the surface that executes it, at the intended revision. Require each unproven claim and gap named with a disposition. Relay them as the oracle's findings, with no code-review findings of yours.
- A reusable verdict is on the PR and names this head, or a shared tree with both SHAs. It comes from a session that the PR's author did not start and does not coordinate, and covers the evidence now offered. An owner's "oracle done" is not one. Do not duplicate a qualifying verdict. A new run, head or evidence outside its scope needs new coverage.
- When a reviewer seat (the agent and model a review runs on) refuses the material, use another approved seat that accepts it, and keep it independent of the author. Seat rules are in [review evidence](review-evidence.md).

## Order and later changes

- Acceptance and the post-acceptance independent FINAL review stay separate from simplify and the pair. Run acceptance on the code head that `ce-simplify-code` produced, before the pair, because a claim that simplify preserved behavior is not a run that shows it.
- If the head moves later, use [head changes](head-changes.md). Never relabel the proof silently.
