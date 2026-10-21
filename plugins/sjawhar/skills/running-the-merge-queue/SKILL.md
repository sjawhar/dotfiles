---
name: running-the-merge-queue
description: "Use when Sami asks a session to manage the open-PR queue, get PRs through review, or tell him which PRs are ready to approve. Also use when a session is merge controller over PRs other agent sessions own. Also when a PR owner reports 'merge-ready' and someone must decide whether that PR ships."
---

# Running the Merge Queue

Owners drive their PRs. You, the controller, verify the gates and merge under current permission.

- Never review code, classify findings, implement, plan, coach or dispatch for owners. Never collect their plans, assign follow-up PRs, relay their questions to Sami or broadcast new rules.
- Never run another owner's E2E, rebase or push their branch, or change CI, validators or models to get a PR through.
- The one review you commission is an independent `oracle` red-team of the end-to-end plan and evidence.
- A repository's one-time exemption needs its actual authorization. It is not a general bypass.
- Never give inspectors write access to a shared workspace.
- Return precise gaps to the owner and continue other ready work.

## 1. Read current authority and priorities

- Use the queue record from the request or the role notes. With none, keep evidence on the existing PR or issue. Never create another tracker or queue issue.
- Record each ruling's source, time and scope: standing approvals (kept through handoffs until superseded), holds, release conditions, priorities, approved windows. Read the record again at each wake and before each merge.
- Never restore a lifted hold or infer a quiet window from history.
- Merge only by an ordinary protected squash merge. No admin bypass, no permission inferred from earlier use. A grant given to this queue session stays live for it: keep merging under it, and ask once for the window the queue needs. Never carry a token in from an earlier task or session.
- At registration, verify the author, session-bound write identity, required reviews and CODEOWNERS. A self-approval restriction is an unmet gate.
- With no explicit priority, sequence by user value and dependencies, including the integration cost of large corpus changes. Never by READY arrival order.
- Ask Sami on the existing Dispatch issue.
- For holds that need a composition test, migration batches, colliding PRs, stacks or a Dispatch answer, read [sequencing](references/sequencing.md).

## 2. Register ownership and act on packets

- Register each PR when it opens: owner, purpose, intended published parent, file scope, required gates, the surface the owner will exercise. Give the gate contract then.
- Act on READY packets and CI events for packeted PRs. Do not sweep all owners.
- Every 30 minutes, find orphans: PRs whose owner is gone and that nobody will packet. Adopt or close them only under your authority. A stalled PR in your queue stays yours.
- Who sends the packet:
  - A PR for a coordinator-owned issue: that coordinator's gate packet, naming the head.
  - An uncoordinated lane: itself, with an independent reviewer's verdict.
  - Anyone but the work owner: before merging, also ask the work owner whether a round is in flight. Green CI cannot tell "finished" from "between pushes".
  - Neither the owner nor you can replace an active gate lane's packet.
- A self-verdict packet begins `SELF-VERDICT - needs clearance or an independent reader`. Hold it for the actual clearance or independent verdict, never one inferred from an earlier general approval.
- For each surfaced head, record the registration facts, the gate lane and each fact that steps 3 to 6 verify. Give each its read time and source link. Add the disposition and the next responsible action.
- GitHub is the live truth, not your record. Re-read held items there at each wake before you write "unchanged".
- Use supervised background watchers, not foreground CI polling.
- Never ask for a rebase or push only to pick up main when there is no conflict or real content change. For stale-main reds, empty results or missing logs, read [evidence quality](references/evidence-quality.md).

## 3. Verify findings and review scope

Before you declare READY or merge, read [review evidence](references/review-evidence.md).

- Enumerate all authors and all pages of review threads, review bodies, issue comments, the PR body and linked verdict artifacts. Zero unresolved threads is not zero open findings. Read each whole verdict, not a grep for severity markers.
- Bind each artifact to its subject, reviewed SHA, author and finding scope. An unrelated green artifact never cancels another's open finding, in either direction. A narrow delta review does not clear the whole PR.
- Before merging, find each claimed closure posted on the PR.
- The owner dispositions findings and the independent reviewer states that nothing blocking remains. You verify the enumeration and dispositions, not code or severity. On disagreement, return the exact undispositioned list.

| Finding | Disposition |
|---|---|
| Correctness or security defect in shipped behavior | Blocking. Fixed in this PR |
| Test that would pass on the bug it is offered to prove | Blocking. Fixed in this PR |
| Other valid finding | Fixed, or a named destination (follow-up PR, existing backlog item, next lane task) in the thread reply and merge record, thread resolved. "Later" is not a destination |
| Refuted | Owner's evidence plus independent confirmation, never a silently resolved thread |
| Human thread | Blocking until the human resolves it |

- One push per review round, not per finding. Never tell an owner to stop pushing a correctness fix. Do not turn minor wording or disposition updates into new campaigns.

## 4. Bind evidence to the head

- Take the reviewed SHA from the review body, not `commit_id`, which can name the head at posting time. The body SHA is still a claim to verify. If it is missing or contradictory, ask. Never substitute metadata.
- When the head moved, read [head changes](references/head-changes.md) before you keep any evidence. Prove ancestry before you treat a change as an appended delta. A rewritten head needs explicit current-head confirmation backed by a runnable comparison of the PR's own contributions.
- Missing inputs, unreachable revisions and a comparison that did not run are gaps, not equivalence. Never read hunks yourself to call a change harmless.
- Required acceptance runs on the intended current head in every case.

## 5. Verify all gates

### CI and scope

- The required aggregate and required lanes are green at the head. Identify superseded cancelled runs by run ID. "Expected red" and in-progress are not green.
- Verify mergeability, declared holds and the intended base separately.
- Compare the packet's files and count with the paginated PR files API, and its commits with the PR commits API, not local `HEAD`.
- The remote branch tip must match the PR's head association. If not, do not merge until the intended head is clear.
- Return an unexplained scope increase or decrease to the owner.
- The PR names its Dispatch issue in title, body, branch or commit message, outage fast paths included. In a public repository, use the bare key, never an internal link. Fix a missing key in the body, without a push.

### Independent review, simplify and thermonuclear

- An independent review artifact on the PR names the applicable head, with all blocking findings closed and all others dispositioned. Self-approval is not one.
- For `/sdd` work, the READY packet also names the post-acceptance FINAL reviewer head. The thermonuclear pair does not replace those gates.
- Runtime code, CI configuration, scripts, Dockerfiles and infrastructure:
  1. Finish the ordinary review rounds.
  2. The owner (under `/sdd`, the coordinator) runs `ce-simplify-code` once, with GPT reviewers, on the PR's own diff. If it changes code, that is the final code head. Rerun CI and acceptance for the affected surface there.
  3. The owner (under `/sdd`, the coordinator) runs the deep + quality thermonuclear pair once at the final code head, as the last review before merge. Both verdicts and dispositions go on the PR.
- Simplify is the last code change and the pair is the last review: not per push, not after merge.

| Scoped exception (never a runtime-code waiver) | Needs |
|---|---|
| Documented minor-only prose round after the pair | Keeps verdict and pair. Verify its scope by the head-change procedure, not the owner's word |
| Prose, comment or docstring-only PR | One reviewer. No simplify, no pair |
| Test-only PR | One reviewer, no pair, if adversarial exercises show the assertions are non-vacuous in both directions and no gate was deleted or loosened |
| Test-only delta on a paired PR | Keeps the pair, with reviewer confirmation of the new head |

- Seats: read the actual reports and substitution disclosures. A rubric loaded into a different seat is not the named review. A rubric name does not prove the model, depth, tool access or cross-model independence. A quota error, provider error or refusal is neither a verdict nor a finding. Use the approved route, disclose the actual seat, and keep the failed attempt recorded. Never silently swap a cross-model review for the same family. Never turn one failed call into a fleet-wide policy.

### Pre-merge acceptance

- Every change to what runs needs its changed end-to-end scenarios before merge: application, infrastructure, tasks, agents, harnesses, and their instructions and configuration. A `.md` or `.yaml` suffix does not make an operative change prose-only. Its consumer path is the acceptance surface.
- The developer owns the proof on a production-like surface, and its later production verification. CI, unit tests, an internal helper or testing after merge do not replace it.
- Each required scenario row records the behavior or contract, the consuming component with its revision or config, and the environment. For a shared-tree identity, it names both SHAs and the shared tree. It records the entry point and exact command, run ID, outcome, duration and evidence link. Its state is one of:
  - `RAN`
  - `BLOCKED`, including never run. It stops readiness.
  - `WAIVED-BY-SAMI`: only Sami's explicit waiver of the named blocker, linked with its scope. It covers no other scenario.
- Return infrastructure, tooling and skill gaps to the owner to fix before merge. Never run their acceptance or invent a waiver.
- For behavior-changing PRs, read [acceptance](references/acceptance.md) before you verify the evidence. Reuse a qualifying oracle verdict already posted on the PR. Otherwise run a read-only `oracle` task for the missing coverage only. Require a disposition for each oracle gap. A claimed but unposted verdict is testimony.
- Genuinely non-operative prose has no runtime scenario and no oracle.

## 6. Merge through the protected, pinned path

Before any write, read [merge mechanics](references/merge-mechanics.md).

- Refresh authority, owner completion, head and branch identity, CI, findings and evidence, all for one SHA.
- The merge operation re-reads its gates, refuses on failure, and pins the verified head SHA. Printed output followed by an unconditional command is not a gate.
- Never bypass branch protection or self-approval restrictions. A moved head means verify again, never an unpinned retry.
- The squash title and message carry the verified head, CI run links, review and acceptance artifacts, and justified review reuse. They also carry any scoped waiver and the named follow-up destinations. Read the resulting commit message back. An APPROVE body is not the squash record.
- Update the queue or PR record with the merge result.

## 7. Close the loop

- Use `post-merge`. Name the developer's production proof, its expected signature and its owner.
- For a stack, enumerate the merged PR's children at once. Send each owner the parent result, actual base and required restack in one message. Never do their rebase. [Sequencing](references/sequencing.md) covers squash parents, native stacks and shared-store safeguards.
- After the green light, the owner runs `ce-compound`. Useful learnings go into the repository's docs-only follow-up path in their next working block, never into another push to the merged PR.
- After a slow round, the owner reviews its own transcript for avoidable waits. Do the same for your own work.
