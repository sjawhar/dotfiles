---
name: running-the-merge-queue
description: "Use when Sami asks a session to manage the open-PR queue, get PRs through review, tell him what's ready to approve, or act as the merge controller over PRs other agent sessions own. Also when a PR owner reports 'merge-ready' and someone has to decide whether that PR ships."
---

# Running the Merge Queue

Owners drive their PRs. The controller verifies the gates and merges under current
permission. You are not a code reviewer, implementer, planner, coach or dispatcher
for other owners. Do not collect their plans, assign follow-up PRs, relay their
questions to Sami, or broadcast new rules. The one review you commission is an
independent oracle's red-team of the end-to-end plan and evidence.

## 1. Read current authority and priorities

Find the existing queue record from the request or authoritative role notes. If
none is supplied, keep evidence on the existing PR/issue; do not invent another
tracker or queue issue. Record each ruling's source, timestamp and scope. Re-read
this record at each wake and before merging.

- Preserve standing approvals through handoffs until superseded. Do not ask again.
- Keep active holds, release conditions, priorities and approved windows in that
  current record. Never restore a lifted hold or infer a quiet window from history.
- Use ordinary protected squash merges. No admin bypass and no permission
  inferred from earlier use. A grant given to this queue session stays live for
  it: keep merging under it and ask once, for the window the queue needs. What is
  banned is carrying a token in from an earlier task or session.
- Verify author, session-bound write identity, required reviews and CODEOWNERS
  requirements at registration. A self-approval restriction is an unmet gate.
- With no explicit priority, sequence by user value and dependencies, not READY
  arrival order. Include the integration cost of large corpus changes.

Read [sequencing](references/sequencing.md) for holds needing composition checks,
coordinated migration batches, colliding PRs, stacks or a native Dispatch answer.
Ask questions requiring Sami's authority, taste or risk appetite on the existing
Dispatch issue. Make ordinary engineering and sequencing decisions yourself.

## 2. Register ownership; act on packets

Register a PR when it opens: owner, purpose, intended published parent, file scope,
required gates and the surface the owner will exercise. Give the gate contract then.
Act on READY packets and CI events for packeted PRs, not periodic sweeps of all
active owners. The standing orphan check is every 30 minutes: identify PRs whose
owner is gone and which nobody will packet; adopt or close only under your authority.
A stalled PR within your assigned queue does not disappear because nobody reported it.

**Gate owner and work owner answer different questions.** A PR delivering a
coordinator-owned issue needs that coordinator's gate packet naming the head. An
uncoordinated lane may packet itself with an independent reviewer's verdict. If the
packet comes from anyone other than the PR's work owner, confirm with that owner
that no round is in flight before merging. Green CI cannot tell finished from
between pushes. Neither the owner nor the controller substitutes for an active
gate lane's packet.

A self-verdict packet must begin `SELF-VERDICT - needs clearance or an independent
reader`. Hold for the actual clearance or independent verdict; do not infer it
from an earlier general approval.

For every surfaced head, record checked-at time and source links for:

- PR, owner, gate lane, current head, branch tip, base, purpose and file list;
- required CI and run IDs, mergeability, active holds and review artifacts;
- all finding dispositions, reviewer/pair heads and applicable simplify result;
- each acceptance scenario, consuming component, revision, run/observation,
  outcome and duration, oracle artifact and any named waiver;
- disposition and next responsible action.

GitHub is live truth, not a derived queue database. Re-read held items on each wake;
write “unchanged” only after a fresh read. Use supervised background watchers, not
foreground CI polling. For stale-main reds or missing logs, read
[evidence quality](references/evidence-quality.md); do not request a rebase or push
merely to pick up main when there is no conflict or real content change.

## 3. Verify the packet, findings and review scope

Before declaring READY or merging, read
[review evidence](references/review-evidence.md). Enumerate **all authors and all
pages** of review threads, review bodies and issue comments, plus the PR body and
linked gate/verdict artifacts. Zero unresolved threads is not zero open findings.
Read the whole relevant verdict, not a grep for severity markers.

For every artifact, identify its subject, reviewed SHA, author and finding scope.
An unrelated green artifact never cancels another artifact's open finding, in
either direction. A narrow delta review does not clear the whole PR. Verify that
a claimed closure is actually posted before merging.

The owner dispositions findings; the independent reviewer confirms that nothing
blocking remains. You verify the enumeration and dispositions, not the code or
severity yourself. Return a disagreement with the exact undispositioned list.

- Correctness/security defects in shipped behavior and tests that would pass on
  the very bug offered as their proof are blocking: fix them in this PR.
- Other valid findings may go to a named follow-up PR, existing backlog item or
  next lane task. Reply with the destination, resolve the thread and include the
  destination in the merge record. “Later” without a destination is not disposition.
- A refuted finding needs the owner's evidence and independent confirmation, not
  a silently resolved thread. Human threads remain blocking until the human resolves
  them. Every valid finding gets fixed or a permitted named destination.
- One push per review round, not per finding. Never tell an owner to stop pushing a
  correctness fix. Do not turn minor wording or disposition updates into new campaigns.

## 4. Bind evidence to the head

Take the reviewed SHA from the review **body**, not its `commit_id`: metadata can
name the head at posting time rather than the commit the reviewer read. Ask for a
missing or contradictory reviewed-head claim; never substitute current metadata.

When the head moved, read [head changes](references/head-changes.md) before deciding
what evidence remains valid. Prove graph ancestry before treating anything as a
post-verdict appended delta. A forward merge can retain the reviewed ancestor;
a rewritten rebase or amend cannot claim that ancestry.

A rewritten head needs explicit current-head confirmation supported by a runnable
comparison of the PR's own old and new contributions, with reachable old/new bases
and tips. An identical clean-rebase contribution need not trigger a whole new pair;
it is not proof that the old SHA became an ancestor. Changed contributions require
review of the change. Required acceptance still runs on the intended current head.
Missing inputs, unreachable revisions and a comparison that did not run are gaps,
not equivalence. Do not inspect hunks yourself to pronounce a change harmless.

## 5. Check all gates

### CI and scope

Required aggregate and required lanes must be green at the head to merge. Identify
superseded cancelled runs by run ID; “expected red” or in-progress is not green.
Check mergeability, declared holds and the intended base separately. Compare the
packet's file list/count with the paginated PR files API, and its commit claims
with the PR commits API, not local `HEAD`. Check the remote branch tip against the
PR's head association; disagreement stops a merge until the intended head is clear.
An unexplained scope increase or decrease goes back to the owner.

The PR must name its Dispatch issue in title, body, branch or commit message. In a
public repository use the bare issue key, never an internal link. A missing key is
fixed in the body without another push; the requirement includes outage fast paths.

### Independent review, simplify and thermonuclear

Require an independent review artifact on the PR naming the applicable head, with
all blocking findings closed and all others dispositioned. Self-approval is not it.
For work produced under `/sdd`, the READY packet also names the post-acceptance FINAL
reviewer head; the thermonuclear pair does not replace those review gates.

For runtime code, CI configuration, scripts, Dockerfiles and infrastructure:

1. Finish the ordinary review rounds.
2. Owner runs `ce-simplify-code` once, with GPT reviewers, on the PR's own diff.
   If it changes code, that result is the final code head: rerun CI and acceptance
   for the affected surface there.
3. Owner runs the deep + quality thermonuclear pair once at that final code head,
   as the last review before merge. Post both verdicts and dispositions on the PR.

Simplify is the last code change; the pair is the last review, not a per-push ritual
or an after-merge task. A documented minor-only prose round can retain its verdict
and pair; verify its scope under the head-change procedure, not by owner assertion.
Genuinely prose/comment/docstring-only PRs need one reviewer, neither code pass.
Test-only PRs may use one reviewer without the pair when adversarial exercises show
assertions are non-vacuous in both directions and no gate was deleted or loosened.
A test-only delta on an already paired PR can retain the pair, with reviewer
confirmation of the new head. These are scoped exceptions, not a runtime-code waiver.
Inspect substitution disclosures: loading a rubric into a different seat is not the
named review. A provider error or policy refusal is not a verdict; use the approved
review route and record any substitution, not a fleet-wide new policy.

### Actual pre-merge acceptance

**Every change that alters what runs needs the changed end-to-end scenarios before
merge**, including application, infrastructure, tasks, agents, harnesses and their
instructions/configuration. A Markdown or YAML suffix does not make an operative
change prose-only. The developer owns the proof on a production-like surface and
its later production verification. CI, unit tests, an internal helper or testing
after merge is not a substitute.

Each required scenario is `RAN`, `WAIVED-BY-SAMI` or `BLOCKED`. Record what ran,
revision, actual consumer/surface, outcome, duration and evidence link. `BLOCKED`,
including never run, stops readiness. Only Sami's explicit waiver of the **named
blocker**, linked with its scope, earns `WAIVED-BY-SAMI`; it covers no other scenario.
Return infrastructure/tooling gaps to the owner to fix. Do not run their acceptance
for them or invent a waiver.

For behavior-changing PRs, read [acceptance](references/acceptance.md) before
checking the evidence and commissioning its oracle. Reuse an independent oracle
artifact already posted on the PR when it names this head or explicitly shared
tree and covers the actual evidence. Dispatch your own read-only oracle only for
missing coverage or new evidence it did not see. Require each oracle gap's
disposition; a claimed but unposted verdict is testimony. Genuinely non-operative
prose has no runtime scenario and needs no E2E oracle.

## 6. Merge through the protected, pinned path

Before any write, read [merge mechanics](references/merge-mechanics.md). Refresh
current authority, owner completion, head/branch identity, checks, findings and
required evidence. Gate the merge on successful verification, not printed output
followed by an unconditional command. The merge operation must re-read its gates
and refuse on failure, and pin the exact verified head SHA. Never bypass branch
protection or self-approval restrictions. A moved head means re-verify, not retry
an unpinned merge.

Write the actual squash title/message with gate facts, evidence, justified review
reuse and named follow-up destinations. Read the resulting commit message back;
an APPROVE body alone is not the squash record. Keep the existing queue/PR record
current with the merge result.

## 7. Close the loop

Use `post-merge`. Name the developer's production proof, expected signature and
owner. For a stack, enumerate the merged PR's children immediately and send each
owner the parent result and required restack in one message; do not do their rebase.
Follow [sequencing](references/sequencing.md) for squash-parent and native stack
mechanics, including shared-store descendant safeguards.

The owner runs `ce-compound` after the green light and puts useful learnings into
the repository's docs-only follow-up path in their next working block. Do not push
again to the merged PR to record them. After a slow round, have the owner review its
own transcript for avoidable waits; do the same for the controller's work.

## Controller boundaries

Do not review code, classify findings, run another owner's E2E, rebase or push their
branch, or change CI/validators/models to get a PR through. A repository-provided
one-time exemption requires its actual authorization; it is not a general bypass.
Never give inspectors write access to a shared workspace. Return precise gaps and
continue other ready work. Do not turn ordinary engineering calls into user holds.
