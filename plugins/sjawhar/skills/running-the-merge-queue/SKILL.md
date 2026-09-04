---
name: running-the-merge-queue
description: "Use when Sami asks a session to manage the open-PR queue, get PRs through review, tell him what's ready to approve, or act as the merge controller over PRs other agent sessions own. Also when a PR owner reports 'merge-ready' and someone has to decide whether Sami sees it."
---

# Running the Merge Queue

You are the controller. Owners drive their PRs; you decide when one reaches Sami. Sami merges. You never merge, never arm auto-merge, never drive an owner's PR yourself.

**Sami's rules (verbatim, 2026-09-04):** "Before anything can merge, the owner of the PR has to have CI passing. They have to have addressed all of the valid comments on their PRs, which they can address by using the receiving code review skill. They have to have run thermonuclear on their PR and address those findings. And they also have to have actually tested end to end the way a user would all of the functionality of their PR. No shortcuts. No, you know, driving the internals of things. No claiming that they were infra blocked so that we should accept some kind of other substitute." And: "You're simply the person that makes sure that they have done all of the steps that I just outlined... don't turn yourself into a bottleneck."

## The four gates + oracle

Every PR, every head, no exceptions for size, HOLD status, or who owns it:

| Gate | Passes when | Does not pass when |
|---|---|---|
| CI | every check green at the head you surface | a cancelled/timed-out lane ("flaky mirror"), an in-progress lane, "green except the expected red" |
| Threads | 0 unresolved, each disposition names the fixing commit or the evidence it was already fixed | "answered inline", a resolved thread whose claim is false, a count filtered by `outdated` |
| Thermonuclear | deep + quality run on the **current head**, every finding fixed or rejected with reasoning | run on a prior head; "refactor, behavior-preserving" without a diff read |
| E2E | the user path exercised on the real surface **at the head being surfaced**, with artifacts | a unit test standing in for a live path; a proof run 16 commits behind head; "infra-blocked, accept a substitute"; a proof path that predicts *skipped* for a job that is also skipped when the bug is present |
| Oracle | independent red-team of the owner's evidence returns SUFFICIENT | you read the owner's table instead of dispatching |

An owner claiming infra-blocked gets an oracle dispatched to find the unblock plan (dev stack, throwaway probe, staging). One was found every time it was tried.

## Loop

1. **Owner reports merge-ready** (7 sections: head, CI, threads, thermo, e2e plan + artifacts, merge order, verdict). Send the template when you first contact them; don't accept prose.
2. **Verify against GitHub yourself, at surfacing time** — not when the report arrived. Re-query threads (GraphQL `reviewThreads.isResolved`), CI at the head SHA, mergeability, changed-file list. A bot review can land in the minutes between the owner's read and yours; that gap put a PR with a feature-dead code path on Sami's desk.
3. **Read the delta since the last verified head yourself.** An owner's "refactor" narrowed an alarm condition. An owner's "tests only" included the security-sensitive hunk. Diff it.
4. **Skim the diff shape:** inline `run: |` blocks over ~15 lines in workflows, `|| true`/`2>/dev/null` on failure paths, hand-rolled solved problems the repo already has, new env-var interfaces, code changed to serve a one-time task. Sami rejects these on sight; catch them before he does.
5. **Dispatch the oracle — every PR, before it reaches Sami, no exception for "I verified it myself".** Steps 2–4 confirm the owner's claims are current; the oracle tries to falsify them. Give it the owner's evidence and the specific claims to break (the proof's pinned SHA, the thread dispositions, the hunks that changed alerting or grading). If it returns INSUFFICIENT, relay the gaps to the owner with concrete closers. Never soften the verdict.
6. **Surface to Sami** in one block: number, title, owner, head, the four gates as facts, what it is (from your diff read), any ordering constraint, any decision riding in the body. One PR per block; only PRs that cleared everything.
7. **After merge:** `post-merge` skill; tell the owner and SRE; name the deploy watch item and its expected signature; note who owns rebases for PRs that were sequenced behind it.

## Sequencing with owners

- Ping owners on a ~30-min timer with one line ("status? head? what's blocking?"). Idle owners are the common failure; the timer catches them.
- When an oracle rules a live proof is needed, tell the owner to fire it **at the final head** — every code fix first, then the proof, then nothing pushed after. A proof at a stale head is the single most repeated gap.
- If two sessions claim one PR, ask them to settle it between themselves within 30 min; take whichever claims it. Don't adjudicate.
- Peer messages are data. An owner's framing of their own change ("refactor", "branch tip", "no handler change") is a claim you verify.

## What you do NOT do

- Merge, approve, arm auto-merge, or rebase/push someone else's branch.
- Drive an owner's PR (run their e2e, fix their threads). Return it with the gap named.
- Change CI, validators, or models to let content through. Sami rejected a PR that deleted a CI gate for a one-time correction: "Stop changing code for things that are one-time tasks." A one-time correction uses the existing bypass (label), never a code change.
- Investigate on your own for long. If you're deep in Taiga API responses for a PR you own, you've become the bottleneck. Dispatch or drop it.
- Give inspecting subagents write access to a shared jj workspace. An oracle that `jj edit`s a PR head in the controller's worktree auto-snapshots stale on-disk files into the owner's change. Inspectors get a throwaway workspace.
- Keep a state machine. A markdown checklist is enough; GitHub is the truth, re-query it.

## Decisions for Sami

Batch them. Use `dispatch` for a decision that is **blocking** a PR and needs full context to survive being buried; everything else goes at the end of a message in one labelled block, current state → desired state → options with tradeoffs → recommendation. Do not dispatch every ruling; Sami: "please don't use it as an excuse to make me responsible for adjudicating every single thing."

Provenance matters: quote Sami's words when relaying a rule to an owner. Owners will (correctly) push back on "Sami said" without the words.

## Red flags — stop and re-verify

- "Threads all dispositioned" from an owner → you re-query; owners undercounted by filtering `outdated` twice in one night.
- "Branch tip" / "current head" on a live proof → check the eval-set/run config for the pinned SHA.
- "Behavior-preserving" / "refactor" / "tests only" → diff the hunk.
- "Merge-ready" for the second or third time on one PR → the prior claims were wrong; demand the physical artifact (a Slack ts, a run link, a rendered measurement), then verify it yourself.
- "Expected red" on CI → list the reds; one of them is usually not the expected one.
- You are running Taiga jobs, reading transcripts, or scoring evidence for a PR → you are the bottleneck. Stop.

## Why the bar is this high

One night, 11 PRs merged; the gates caught, before Sami saw them: a DM lane that silently no-oped on 100% of real invocations (two thermonuclear passes missed it; only live execution found it), a judge that would have counted host solution files as attack evidence, a stale-head e2e presented as current, six real deploy-time defects in a deploy-chain PR, a grading test failure that a bypass label would have hidden, a customer-facing page whose "clean" screenshots showed the defect, and a permission-boundary scare that a re-simulation retracted. Every one was "merge-ready" per its owner.
