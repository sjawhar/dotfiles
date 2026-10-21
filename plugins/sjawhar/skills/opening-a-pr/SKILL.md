---
name: opening-a-pr
description: "Use before opening a pull request, and before telling Sami that any work is merge-ready, done, or blocked on his merge. Also use when an open PR changed enough that its verification is stale, and when tempted to leave anything for later or after the merge."
---

# Opening a PR

This gate stands between "I believe it works" and a human's review. It owns every PR-opening flow. Other shipping skills supply mechanics inside step 8 and never replace it.

- Do every step, in order, on every change. Scale the depth of evidence to the diff, never the number of steps.
- "Later" means after end-to-end proof and before human review, never after merge.
- When a step links a reference, read it at that step, not from memory.
- Two rules bind a plan or spec as well as the PR:
  - A change to a producer's schema, publish path or nullability: name each downstream surface and what it will show differently. With no known consumer, record the search. The consumer's owner reviews that delta. Sami does not need to approve it.
  - A render that a named person asked for: record that person and the channel where they will see it live. You do not need their reply.

1. **Empty the hardening ledger.** Finish each shortcut, or name its blocker: missing access, or a decision only Sami can make. A follow-up or a GitHub issue is not a legal state.
   - Diff against the target branch and the plan or issue. Finish anything promised but absent.
   - No ledger (interactive work, not `sdd`)? Write one now: the request, settled decisions, every shortcut, every part you are unsure you covered.
   - In agent-c, each item of a required checklist is a ledger row until done.
2. **Have the `oracle` agent audit completeness.** You cannot audit your own scope. Dispatch a fresh `oracle` agent with the diff, the ledger and the authorization. The authorization is the plan or issue, else Sami's request and the settled decisions, quoted. Mandate: *"You did not do this work. List everything the authorization promised that this diff does not deliver, and every hack that is still in it."* Fix and re-dispatch until clean.
3. **Run the `analyze` skill.** Fix critical and high findings, then run it again. One finding survives three rounds: write a checkpoint (tried, failed, learned). Then change approach, or bring Sami options with your recommendation.
4. **Have the `deep` agent prove it works.** Read [references/proof.md](references/proof.md) and dispatch a fresh `deep` agent with its mandate. Fix every defect and re-dispatch until clean. Only a named blocker that Sami explicitly waived exempts this step. While the proof is blocked, do not open the PR for review: report NOT merge-ready.
5. **Sweep the docs.** Grep READMEs, `docs/`, skills and AGENTS.md files for what the diff made wrong: commands, flags, paths, config keys, behavior. Fix them in this change with `updating-docs`.
6. **Get a review from the `oracle` agent** of the final diff and the QA report. Fix its findings. Urgency limits the review to the change and never removes it. Ship an urgent fix with the reviewer already dispatched and the merge gated on the verdict.
7. **Capture the learning.** A gotcha that cost more than half an hour or contradicted the docs: run `ce-compound mode:headless`. Else say so in one line.
8. **Structure the commits and draft the body before you open or update the PR.** Read [references/commits-and-body.md](references/commits-and-body.md).
   - Choose commits by reviewability, not ceremony. A single commit is right for a small change. Decide, and do not ask Sami about commit structure.
   - A contract tightening needs a `## Contract change census`. Read [references/contract-census.md](references/contract-census.md).
9. **Preserve the final tree across history-only rewrites.** Before you reorder, split or squash commits, run the fingerprint in commits-and-body.md. Never push a tree change you did not intend.
10. **Never open a PR against our own fork.** A PR goes upstream only when the change belongs there. Read [references/fork-and-upstream.md](references/fork-and-upstream.md) when `knives repos` lists the repository, when a change touches a fork pin, before you ship a fork member, and before any upstream PR.
11. **Open or update the PR, and default to updating.** Read [references/push-and-open.md](references/push-and-open.md) before the first push, after a red CI run, and before `gh pr create`.
    - Found something broken? Fix it and open the PR. Asking first is the failure.
    - One line of work gets one PR.
    - Before every push, run the fast local checks outside your agent shell's environment. Push only when they pass, and cite the output lines.
    - Never pass `--label` to `gh pr create`. Always pass `--head`.
    - A PR without current, observed `## Verification` evidence is not merge-ready. A PR whose proof is blocked is not opened for review.

## After the PR exists

- Hand off to `landing-a-pr` at once. It owns the `ce-babysit-pr` watch and auto-merge. A migration PR is merged by hand, not by auto-merge. Do not run one manual `gh pr view` and leave.
- The automated review runs once on open and on `ready_for_review`. Every later review is requested, and `landing-a-pr` decides when.
- After the merge, you test the change in production and record what you saw (`landing-a-pr` §6, `post-merge`).
- Before you say done or merge-ready, restate the original goal and point to the step 4 evidence for it.

## Red flags: go run step 4

| Excuse | Reality |
|---|---|
| "CI is green." "The mechanism says it works." | You saw a mechanism, not a result. |
| "Only a pin, a prompt, a doc." "Too small to boot." | What runs changed. Find its consumer and run it. |
| "It gets verified after the merge." "Sami wanted it fast." | Then it is unverified now. |
| "I disclosed the gap." "No test environment here." | Report NOT merge-ready. Do not open it for review. Build the missing surface. |
| "I verified it before the last fixes." | Those fixes are the diff now. |
| "Verified by inspection." | Inspection shows intent, not the result. |
| "Want me to run the smoke test?" | Do not ask. Running it is the job. |
| "Separate PR." "Follow-up issue?" | That is deferral. Do it now. Never open an issue Sami did not ask for. |
| "Nothing is actionable until you merge." | Read this list first. It is usually false. |
