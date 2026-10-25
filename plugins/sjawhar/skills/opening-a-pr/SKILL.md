---
name: opening-a-pr
description: "Use before opening a pull request, and before telling Sami that any work is merge-ready, done, or blocked on his merge. Also use when an open PR changed enough that its verification is stale, and when tempted to leave anything for later or after the merge."
---

# Opening a PR

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

Do every step below, in order, on every change and in every PR-opening flow: a skipped step is how unverified work reaches a human. A skill named inside a step (`analyze`, `updating-docs`, `landing-a-pr`, `pr-preflight`) supplies that step's mechanics and never replaces the step. Scale the evidence to the diff, never the number of steps. "Later" means after the end-to-end proof and before human review, never after the merge. Read a step's reference at that step, not from memory, because these files change.

Two rules bind a plan or spec as well as the PR:

- A change to a producer's schema, publish path or nullability: name each downstream surface and what it will show differently, and let the consumer's owner review that delta. With no consumer found, paste the search you ran and its empty output into the plan, spec or PR's `## Contract change census`. Sami does not approve this.
- A render that a named person asked for: record that person and the channel where they will see it live. You do not need their reply.

1. **Empty the hardening ledger.** Finish each shortcut, or name its blocker: missing access, or a decision only Sami can make. A follow-up or a GitHub issue does not clear a row, because deferred work does not come back. Diff against the target branch and the plan or issue, and finish anything promised but absent. No ledger (interactive work, not `sdd`)? Write one now: the request, the settled decisions, every shortcut, every part you are unsure you covered. In `trajectory-labs-pbc/agent-c`, each item of a checklist that `.github/scripts/checklist_routing.py` requires is a ledger row until it is done.
2. **Have a fresh `oracle` agent audit completeness**, because you cannot audit your own scope. Give it the diff, the ledger and the authorization: the plan or issue, else Sami's request and the settled decisions, quoted. Mandate: *"You did not do this work. List everything the authorization promised that this diff does not deliver, and every hack that is still in it."* Fix and re-dispatch until clean.
3. **Run the `analyze` skill.** Fix critical and high findings, then run it again. One finding survives three rounds: write tried / failed / learned into the ledger, then change approach or bring Sami options with your recommendation.
4. **Have a fresh `deep` agent prove it works.** Read [references/proof.md](references/proof.md) at this step and dispatch with the mandate there. Fix every defect and re-dispatch until clean. Only a named blocker that Sami explicitly waived exempts this step. While the proof is blocked, report NOT merge-ready and do not open the PR for review. Tempted to skip it? Read the excuses in [references/red-flags.md](references/red-flags.md).
5. **Sweep the docs.** Grep READMEs, `docs/`, skills and AGENTS.md files for what the diff made wrong: commands, flags, paths, config keys, behavior. Fix them in this change with `updating-docs`; a stale doc misdirects the next agent.
6. **Get a review from a fresh `oracle` agent** of the final diff and the QA report, and fix its findings. An urgent fix still gets this review, scoped to the diff: dispatch the reviewer before you push and gate the merge on its verdict.
7. **Capture the learning.** A gotcha that cost more than half an hour or contradicted the docs: run `ce-compound mode:headless`. Else say so in one line.
8. **Structure the commits and draft the body before you open or update the PR.** Read [references/commits-and-body.md](references/commits-and-body.md). Choose commits by reviewability, not ceremony: a single commit is right for a small change. Decide this yourself; do not ask Sami about commit structure. A change that tightens a contract needs a `## Contract change census`: read [references/contract-census.md](references/contract-census.md).
9. **Preserve the final tree across history-only rewrites.** Before you reorder, split or squash commits, run the fingerprint in commits-and-body.md and compare it after: a reorder or squash can drop a hunk silently, so the two must match.
10. **Never open a PR against our own fork.** A PR goes upstream only when the change belongs there. Read [references/fork-and-upstream.md](references/fork-and-upstream.md) when `knives repos` lists the repository, when a change touches a fork pin, before you ship a fork member, and before any upstream PR.
11. **Open or update the PR, and default to updating.** Read [references/push-and-open.md](references/push-and-open.md) before the first push, after a red CI run, and before `gh pr create`.
    - One line of work gets one PR. A defect you hit while doing it is part of it: fix it in this PR instead of asking whether to.
    - Before every push, run the fast local checks outside your agent shell's environment. Push only when they pass, and cite the output lines.
    - Never pass `--label` to `gh pr create`, and always pass `--head`.
    - A PR without current, observed `## Verification` evidence is not merge-ready: current means at this head, observed means you ran it.

## After the PR exists

- Hand off to `landing-a-pr` at once: it owns the `ce-babysit-pr` watch and auto-merge, and a PR nobody watches stalls. Do not run one `gh pr view` and leave. A PR that adds or changes a database migration is merged by hand, not by auto-merge.
- The automated review runs once on open and on `ready_for_review`. Request any later review only when `landing-a-pr` says to.
- After the merge, test the change in production and record what you saw (`landing-a-pr` §6, `post-merge`).
- Before you say done or merge-ready, restate the original goal and point to the step 4 evidence for it.
