# Actual pre-merge acceptance and its oracle

Read this for any change to what executes, including infrastructure, task data,
agent/harness instructions, registry selection and CI orchestration. The repo's own
workflow defines its production-like surface. The controller verifies the owner's
proof and commissions its independent oracle; it does not implement or run it.

## What the packet must establish

For each changed end-to-end scenario, record:

1. The user-visible behavior or operational contract being exercised.
2. The component that actually consumes the change, its revision/image/config and
   the target environment. Name both SHAs and the shared tree if using that identity.
3. The user/consumer entry point, exact command or interaction, and run/observation ID.
4. What executed, its outcome and duration, and the evidence link.
5. `RAN`, `WAIVED-BY-SAMI` or `BLOCKED`, and the disposition of any oracle gap.

A build, unit suite, import or low-level probe can be useful groundwork without
being that scenario. A green pipeline is not proof it scheduled the changed path.
An unrun scenario is BLOCKED, not an omitted row. The owner fixes infrastructure,
tooling or skill gaps that prevent proof before merge. Production observation
belongs after that, not in place of it.

A waiver is valid only when Sami explicitly waived the named blocker. Link the
ruling and preserve its PR, revision, scenario, duration or other stated scope.
Do not extend one waiver to another blocker or PR, and do not categorically reject
an actual authorized exception. The state name is `WAIVED-BY-SAMI`.

## Ask the runtime that executes the change

Examples are checks on evidence coverage, not universal commands:

- **Third-party agent, collector or scheduler configuration:** show that runtime
  consuming the configuration and producing the intended result. A helper reading
  the file is not the runtime.
- **Node image or userData:** identify a gating workload on a post-apply node using
  the new runtime. A green run wholly scheduled on old nodes leaves replacement
  behavior unproven; a node-level probe proves only what it exercised.
- **Registry-selected package:** show the exact entries supported launchers request
  resolving and executing in the intended runner environment. Such a package can
  affect future launches without any infrastructure redeploy. Migrate supported
  callers; neither an import nor an assumption that every launch tracks main proves
  availability. Do not require obsolete aliases without a compatibility contract.
- **Tightened acceptance assertions:** exercise them on real data before merging.
  Theory that an assertion is safe does not prove it will pass on valid real runs.
- **Advisory lane:** inspect which scenario ran and what it established. Its label
  neither grants nor denies evidence; do not promote it to a required gate merely
  to recognize a valid observation.
- **Watcher offered as a merge condition:** a start line proves only process start.
  Require the attached run/resource identity, a jobs/state snapshot and a subsequent
  observation. An empty selection query can leave a watcher alive forever.

The observed version belongs beside any live-behavior claim. “Adding X made it
work” is correlation, not the mechanism. Do not relay an inferred mechanism as a
constraint on other owners.

## Independent oracle coverage

Give the read-only oracle:

- the READY packet and exact head;
- PR file list and the body's verification section;
- actual acceptance plan, run/observation artifacts and known open gaps;
- any scoped waiver and the source it relies on.

Ask whether the evidence proves the changed behavior through the surface that
executes it, at the intended revision. Require named unproven claims and gaps,
with an explicit disposition for each. Relay those gaps as the oracle's findings;
do not add controller code-review findings.

A reusable oracle verdict is posted on the PR, names this head or an explicitly
shared tree with both SHAs, comes from a session independent of the PR author lane,
and covers the actual evidence now offered. An owner saying “oracle done” is not
that artifact. Reuse a qualifying posted verdict; do not duplicate it. A new run,
new head or new evidence outside its scope needs coverage, not a blind carry.

A provider refusal or failed call is not an oracle verdict. Use an approved
non-refusing seat when the material requires it, preserve independence and disclose
the actual model/seat. Do not silently replace a requested cross-model review with
the same family or treat quota errors as a new fleet incident.

## Review order and later changes

Acceptance and the post-acceptance independent FINAL review remain distinct from
the simplify pass and thermonuclear pair. If simplify changes code, rerun CI and
the changed acceptance surface on that final code head before the pair. A claim of
behavior preservation is not its execution. If the head moves afterward, use
[head changes](head-changes.md) rather than silently changing the proof's label.

Genuinely non-operative prose needs no runtime E2E and no oracle. Operative prompts,
skills, task definitions and harness configuration do not earn that exemption from
their file suffix. Their applicable consumer path is the acceptance surface.
