---
name: running-the-merge-queue
description: "Use when Sami asks a session to manage the open-PR queue, get PRs through review, or tell him which PRs are ready to approve. Also use when a session is merge controller over PRs other agent sessions own. Also when a PR owner reports 'merge-ready' and someone must decide whether that PR ships."
---

# Running the merge queue

Owners drive their PRs. You, the controller, check the gates and merge under the permission that holds now. A packet is the READY message for one PR: its head SHA and the evidence for each gate below.

## Limits

- Never review code, classify findings, implement, plan or coach for owners, and never send subagents to do an owner's work. Never collect their plans, assign follow-up PRs, relay their questions to Sami or broadcast new rules. Each of these makes you a second owner and stalls every other PR in the queue.
- Never run another owner's E2E, rebase or push their branch, or change CI, validators or models to get a PR through: a gate you changed no longer tests their work.
- The one review you commission is a read-only `oracle` task that red-teams the end-to-end plan and evidence (step 5).
- Never give a reviewer or `oracle` task you commission write access to a workspace another session uses, because it could change the tree it judges.
- Send the owner the exact gap and go on with other ready work, so one stuck PR does not stop the queue.

## 1. Read current authority and priorities

- Use the queue record named in Sami's request or in the notes your role started with. With none, keep evidence on the existing PR or issue. Never create another tracker or queue issue: a second record splits the history.
- Record each ruling's source, time and scope: standing approvals (kept through handoffs until superseded), holds, release conditions, priorities, approved windows. Read the record again at each wake and before each merge, because rulings change while you wait.
- Never restore a lifted hold or infer a quiet window from history: only a current ruling sets them.
- Merge only by an ordinary protected squash merge, never under a permission inferred from earlier use. A grant given to this queue session stays live for it: keep merging under it, and ask Sami once for the merge window the queue needs (a time span in which you may merge READY PRs without asking per PR).
- At registration, check the author, the GitHub account this session writes as (`gh api user --jq .login`), required reviews and CODEOWNERS. A self-approval restriction is an unmet gate.
- With no explicit priority, merge first what Sami's stated goal needs, then parents before children, and a large corpus change before PRs that would have to integrate with it. Never by READY arrival order, which rewards speed over value.
- Ask Sami on the existing Dispatch issue, so the answer stays with the work.
- For holds that need a composition test, migration batches, colliding PRs, stacks or a Dispatch answer, read [sequencing](references/sequencing.md).

## 2. Register PRs and act on packets

- Register each PR when it opens: owner, purpose, intended published parent, file scope, required gates, the end-to-end scenarios the owner will run before merge. Give the owner the gate list then, so the packet arrives complete.
- Act on packets and on CI events for PRs that have one. Do not sweep all owners.
- Owners drive their PRs while they are live. Every 30 minutes, find orphans: PRs whose owner is gone and that nobody will packet. Close an orphan or give it a new owner only under a current ruling from Sami that names it. Until then it stays yours: keep it registered and report it in each queue status, so it is not lost.
- A gate session is the session a coordinator (the `sdd` lead for the issue) assigned to check this PR's gates. Who sends the packet:

| PR | Packet |
|---|---|
| For a coordinator-owned issue | That coordinator's gate packet, naming the head |
| From an uncoordinated session | The session itself, with an independent reviewer's verdict |
| Sent by anyone but the work owner | Also ask the work owner whether a round is in flight before merging: green CI cannot tell "finished" from "between pushes" |
| With an active gate session | Only that session's packet; neither the owner nor you can replace it |

- A self-verdict packet begins `SELF-VERDICT - needs clearance or an independent reader`. Hold it until Sami approves this PR by name or an independent reviewer posts a verdict on the PR that names the head, never one inferred from an earlier general approval.
- For each head you see, record the registration facts, its gate session and each fact steps 3 to 5 check, each with its read time and source link. Add the disposition and who acts next.
- GitHub is the live truth, not your record. Re-read held PRs there at each wake before you write "unchanged".
- Start CI waits as background jobs (for example an async `gh pr checks N --watch`) and keep working; a foreground wait blocks your turn.
- Never ask for a rebase or push only to pick up main when there is no conflict or real content change: each push restarts CI and review. For stale-main reds, empty results or missing logs, read [evidence quality](references/evidence-quality.md).

## 3. Check findings

Before you declare READY or merge, read [review evidence](references/review-evidence.md). It holds the surfaces to read, the disposition of each finding, the smaller gates for prose-only and test-only PRs, and review seats.

- The owner dispositions findings and the independent reviewer states that nothing blocking remains. You check the enumeration and the dispositions, not code or severity. On disagreement, return the exact list of undispositioned findings.

## 4. Bind evidence to the head

- Take each reviewed SHA from the review body, as [review evidence](references/review-evidence.md) says. When the head moved, read [head changes](references/head-changes.md) before you keep any evidence. Prove ancestry before you treat a change as an appended delta. After an amend or rebase, keep the old review only when the reviewer posts a comment naming the new head, backed by the contribution comparison in head-changes section 3. Never read hunks yourself to call a change harmless.

## 5. Check every gate

- Check CI, mergeability, holds, the base, the file and commit scope and the Dispatch key at the head, each as [merge mechanics](references/merge-mechanics.md) lists them, before you declare READY.
- Review: an independent review artifact on the PR names the applicable head, with all blocking findings closed and all others dispositioned. Self-approval is not one.
- Runtime code, CI configuration, scripts, Dockerfiles and infrastructure, never waived: after the ordinary review rounds, the owner (under `sdd`, the coordinator) runs `ce-simplify-code` once, then the `thermonuclear-deep-review` and `thermonuclear-code-quality` agents (the thermonuclear pair) once at the final code head. Simplify is the last code change and the pair the last review, so the last review sees the code that ships. The order and reruns are in [review evidence](references/review-evidence.md).
- Acceptance: every change to what runs needs its changed end-to-end scenarios run at the intended current head before merge: application, infrastructure, tasks, agents, harnesses, and their instructions and configuration. A `.md` or `.yaml` suffix does not make an operative change prose-only: the scenario must run through the program that reads that file. Prose that changes nothing that runs needs no scenario and no oracle.
- Before you check that evidence, read [acceptance](references/acceptance.md): who owns the proof, the state of each scenario, and when to run the `oracle`.

## 6. Merge through the protected, pinned path

Before any write, read [merge mechanics](references/merge-mechanics.md).

- Refresh every gate for one SHA and merge only by the pinned command there. Never bypass branch protection or self-approval restrictions. A moved head means check again, never an unpinned retry.
- Update the queue or PR record with the merge result.

## 7. Close the loop

- Use `post-merge`. Name the production check the developer will run, the log line, metric or output that shows the change working, and who runs it.
- When the merged PR has stacked children, tell their owners at once, as [sequencing](references/sequencing.md) says, so no child builds on a deleted parent.
- After that production check passes, the owner runs `ce-compound` and puts useful learnings in a new docs-only PR before it starts other work, never in a push to the merged PR, which would restart its gates.
- After a round with waits, the owner reads its own transcript and lists each wait on a check or reply it could have started earlier, so the next round starts it sooner. Do the same for your own work.
