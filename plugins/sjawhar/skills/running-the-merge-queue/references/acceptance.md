# Pre-merge acceptance and its oracle

Read for any change to what executes: infrastructure, task data, agent or harness instructions, registry selection, CI orchestration. The repository's workflow docs name which environment counts as production-like. You verify the owner's proof and commission its oracle; you never implement or run the proof.

## Evidence rules

- The proof is `opening-a-pr`'s step 4 (its `proof.md`): the owner runs it on a production-like surface, with the markers `RAN`, `BLOCKED`, `WAIVED-BY-SAMI`. The queue adds:
- The owner also owns the later production check. It comes after the proof, never in its place. CI, unit tests, an internal helper or testing after merge do not replace the proof.
- A build, unit suite, import or low-level probe is groundwork, not the scenario. A green pipeline does not prove that it scheduled the changed path.
- Each required scenario row records the behavior or contract, the consuming component with its revision or config, the environment, the entry point and exact command, run ID, outcome, duration and evidence link. A row that claims two commits have the same tree names both SHAs and the tree hash.
- An unrun scenario is a `BLOCKED` row, never a missing row, and it stops readiness.
- Return infrastructure, tooling and skill gaps to the owner to fix before merge. Never run their acceptance or invent a waiver.
- Keep a waiver's stated scope: PR, revision, scenario, duration. It covers no other blocker or PR. When Sami's waiver covers the blocker, accept it; do not ask for the waived scenario anyway.
- Put the observed version beside each live-behavior claim. "Adding X made it work" is correlation, not mechanism: never relay an inferred mechanism as a constraint on other owners.

## Ask the runtime that executes the change

- Third-party agent, collector or scheduler config: that runtime consuming the config and giving the result. A helper reading the file is not the runtime.
- Node image or userData: a gating workload on a post-apply node with the new runtime. A green run all on old nodes proves no replacement behavior; a node probe proves only what it exercised.
- Registry-selected package: the entries supported launchers request, resolving and executing in the intended runner. It can change future launches with no redeploy, so migrate supported callers. An import, or "every launch tracks main", proves no availability. Keep an old alias only where a documented compatibility promise requires it.
- Tightened acceptance assertions: a run on real data before merge. A safe theory does not prove valid real runs pass.
- Advisory lane: which scenario ran and what it showed. Accept a valid observation from an advisory lane as evidence; it need not become a required check first.
- Watcher offered as a merge condition: the attached run or resource identity, a jobs or state snapshot, and a later observation (the watcher's output after at least one full poll interval or one watched event, showing that it read the watched run or resource). A first log line proves only that the process exists, and an empty selection query can keep a watcher alive forever.

## Independent oracle

- Reuse a qualifying oracle verdict already posted on the PR. Otherwise run a read-only `oracle` task for the missing coverage only, and require a disposition for each gap it names. A verdict claimed but not posted on the PR is not evidence: ask for it on the PR.
- Give the `oracle` task the READY packet and exact head, the PR file list with the body's verification section, the acceptance plan and artifacts, known gaps, and any scoped waiver with its source.
- Ask whether the evidence proves the changed behavior through the surface that executes it, at the intended revision, with each unproven claim and gap named. Relay them as the oracle's findings, with no code-review findings of yours.
- A reusable verdict is on the PR and names this head, or a shared tree with both SHAs; comes from a session the PR's author did not start and does not coordinate; and covers the evidence now offered. An owner's "oracle done" is not one. A new run, head, or evidence outside its scope needs new coverage.
- When a reviewer seat (the agent and model a review runs on) refuses the material, use another approved seat that accepts it, still independent of the author. Seat rules: [review evidence](review-evidence.md).

## Order and later changes

- Run acceptance on the code head that `ce-simplify-code` produced, before the thermonuclear pair: a claim that simplify preserved behavior is not a run that shows it. Acceptance and the independent FINAL review after it stay separate from simplify and the pair.
- If the head moves later, use [head changes](head-changes.md). Never relabel the proof silently.
