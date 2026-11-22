---
name: running-the-merge-queue
description: "Use when Sami asks a session to manage the open-PR queue, get PRs through review, or say which PRs are ready to approve; when a session is merge controller over PRs other sessions own; or when a PR owner reports merge-ready and someone must decide whether that PR ships."
---

# Running the merge queue

Owners drive their PRs. You, the controller, check the gates and merge under the permission that holds now. A packet is the READY message for one PR: its head SHA and the evidence for each gate below.

## Limits

- Do not review code, classify findings, implement, plan or coach for owners, or send subagents to do an owner's work: each makes you a second owner and stalls the rest of the queue.
- Do not collect owners' plans, assign follow-up PRs, relay their questions to Sami or broadcast new rules: each makes you a second owner and stalls the rest of the queue.
- Do not run another owner's end-to-end scenarios, rebase or push their branch, or change CI, validators or models to get a PR through: a gate you changed no longer tests their work.
- The one review you commission is a read-only `oracle` task that red-teams the end-to-end plan and evidence (step 5).
- Give no reviewer you commission write access to a workspace another session uses.
- Send the owner the exact gap.
- Then go on with other ready work.

## 1. Read current authority and priorities

- Use the queue record named in Sami's request or in your role's starting notes.
- With no queue record named, keep evidence on the existing PR or issue.
- Create no other tracker or queue issue.
- Record each decision's source, time and scope: standing approvals (kept through handoffs until superseded), holds, release conditions, priorities, merge windows.
- Read the record again at each wake and before each merge.
- Only a current decision sets a hold or a quiet window: never restore a lifted hold or infer a window from history.
- Merge only by an ordinary protected squash merge.
- Never merge under a permission inferred from earlier use: a grant given to this queue session stays live for it.
- Ask Sami once for a merge window: a span to merge READY PRs without asking per PR.
- At registration, check the author, the account this session writes as (`gh api user --jq .login`), required reviews and CODEOWNERS.
- An approval that account cannot give is unmet until `landing-a-pr`'s repo merge rules name who gives it.
- **Read `references/sequencing.md` now** before you decide the merge order:
  - the order with no explicit priority (Sami's goal, parents, corpus changes);
  - never by READY arrival order.
- Ask Sami with `dispatch ask` on the Dispatch issue the PR names, or on the queue record for a question about the whole queue. Dispatch is the issue tracker the `dispatch` skill covers.
- Read `references/sequencing.md` for holds that need a composition test, migration batches, colliding PRs, stacks or reading a Dispatch answer.

## 2. Register PRs and act on packets

- Register each PR when it opens: owner, purpose, intended published parent, file scope, required gates and the end-to-end scenarios the owner runs before merge.
- Give the owner the gate list at registration, so the packet arrives complete.
- Act on packets and on CI events for PRs that have one.
- Do not sweep all owners.
- Owners drive their PRs while they are live.
- Every 30 minutes, find orphans: PRs whose owner is gone (`envoy_sessions` lists its session as neither live nor unresponsive) and that nobody will packet.
- Close or reassign an orphan only under a current decision from Sami that names it.
- Until such a decision, an orphan stays yours: keep it registered and report it in each queue status.

| PR | Who sends the packet |
|---|---|
| For an issue a coordinator (the `sdd` lead) owns | That coordinator's gate packet, naming the head |
| With an active gate session (the session a coordinator assigned to check its gates) | That session's packet or a verdict on the PR naming the head from any other reviewer who is not the author |
| From an uncoordinated session | The session itself, with an independent reviewer's verdict |

- A packet from anyone but the work owner: also ask the owner whether a round is in flight before you merge, since green CI cannot tell "finished" from "between pushes".
- Hold a self-verdict packet until Sami approves that PR by name or an independent reviewer posts a verdict on the PR that names the head. A self-verdict packet begins `SELF-VERDICT - needs clearance or an independent reader`.
- For each head, record the registration facts, its gate session and each fact steps 3 to 5 check, each with its read time and source link, plus the disposition and who acts next.
- Re-read held PRs on GitHub at each wake before you write "unchanged": GitHub is the live truth, not your record.
- Start CI waits as background jobs (like an async `gh pr checks N --watch --interval 120`).
- Keep working while a CI wait runs.
- Ask for a rebase or push only when main conflicts with the PR or the PR's own diff needs a change, never only to pick up main: each push restarts CI and review.
- Read `references/evidence-quality.md` for stale-main reds, empty results or missing logs.

## 3. Check findings

Before you declare READY or merge, read `references/review-evidence.md`: the surfaces to read, each finding's disposition, the smaller gates for prose-only and test-only PRs, and review seats.

- You check the enumeration and dispositions, not code or severity. The owner dispositions findings and the independent reviewer states that nothing blocking remains.
- On disagreement, return the exact list of undispositioned findings.

## 4. Bind evidence to the head

- Take each reviewed SHA from the review body.
- When the head moved, read `references/head-changes.md` before you keep any evidence.
- Prove ancestry before you treat a change as an appended delta.
- After an amend or rebase, keep the old review only when the reviewer posts a comment naming the new head, backed by the contribution comparison in head changes.
- Never read hunks yourself to call a change harmless.

## 5. Check every gate

- Run `pr-gate <owner/repo> <N> <head>` for GitHub's side of the gates.
- Read `pr-gate`'s reasons, not only its exit code.
- CI, mergeability, holds, the base, the file and commit scope and the Dispatch key at the head: check each as `references/merge-mechanics.md` lists them.
- Review: an independent review artifact on the PR names the head, with every blocking finding closed and every other one dispositioned. Your approval is not one.
- Runtime code, CI configuration, scripts, Dockerfiles and infrastructure:
  - After the ordinary review rounds, the owner (under `sdd`, the coordinator) runs `ce-simplify-code` once.
  - Then the owner (under `sdd`, the coordinator) runs the thermonuclear pair (`thermonuclear-deep-review`, `thermonuclear-code-quality`) once at the final code head.
  - Neither run is ever waived.
  - Order and exceptions: `references/review-evidence.md`.
- Acceptance: every change to what runs needs its changed end-to-end scenarios run at the intended head before merge.
- A `.md` or `.yaml` suffix does not make an operative change prose-only: the scenario runs through the program that reads that file.
- Prose that changes nothing that runs needs no scenario and no oracle.
- Read `references/acceptance.md` before you check acceptance evidence.

## 6. Merge through the protected, pinned path

- Read `references/merge-mechanics.md` before any write.
- Refresh every gate for one SHA.
- Merge only by the pinned command in merge mechanics.
- Never bypass branch protection.
- A moved head means check again, never an unpinned retry.
- Update the queue or PR record with the merge result.

## 7. Close the loop

- After each merge and each round with waits, read `references/close-the-loop.md` and do each item: the production check, stacked children, the learnings PR and the list of waits.
