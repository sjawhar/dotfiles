---
name: opening-a-pr
description: "Use when about to open a pull request or change an open one's head or body, when an open PR's head is not the one its verification ran at, before telling Sami that work is merge-ready, done or blocked on his merge, or when tempted to leave work for later or after the merge."
---

# Opening a PR

Open or update a complete, proven, reviewed PR; hand it to `landing-a-pr`.

**Done when:** you handed the PR to `landing-a-pr` at once, and before you said done or merge-ready, you restated the original goal and pointed to the step 4 evidence for it.

- Do every step, in order, on every change.
- A skill named in a step (`analyze`, `updating-docs`, `pr-preflight`, `landing-a-pr`) supplies that step's mechanics and never replaces the step.
- The diff sets how much evidence a step needs, through the scenarios it affects (step 4). It never sets how many steps you do.
- "Later" means after the step 4 proof and before human review, never after the merge.
- Read each step's reference at that step, not from memory: the references change.
- Read [references/plan-and-pr-sections.md](references/plan-and-pr-sections.md) for a plan, spec or PR of a change that tightens a contract, changes a producer's schema, publish path or nullability, or makes a render a named person asked for.

Steps 2, 4 and 6 each use a fresh, strong agent:

- The agent of steps 2, 4 and 6 did not write the change.
- The agent of steps 2, 4 and 6 runs a strong model rather than a cheap one (`using-subagents`).
- The agent of steps 2, 4 and 6 is from a different model family than the author's where your stream's brief permits one.

Your stream's brief is the standing instructions your stream's agents work from (the task this session started from names it). It maps each capability to an agent type, for example `oracle` for review and `deep` for proof; a Claude-only brief gets a fresh Claude.

1. **Empty the hardening ledger.** Finish each shortcut, or name its blocker: missing access, or a decision only Sami can make. A follow-up or a GitHub issue does not clear a row. Diff against the target branch and the plan or issue, and finish anything promised but absent. No ledger (no plan with a `## Hardening ledger` section, which `sdd` plans carry)? Write one now: the request, the settled decisions, every shortcut, every part you are unsure you covered. Where the repository has `.github/scripts/checklist_routing.py`, each item of a checklist it requires is a ledger row until it is done.
2. **Have a fresh, strong review agent audit completeness**, because you cannot audit your own scope. Give it the diff, the ledger and the authorization: the plan or issue, else Sami's request and the settled decisions, quoted. Mandate: *"You did not do this work. List everything the authorization promised that this diff does not deliver, and every hack that is still in it."* Fix and re-dispatch until clean.
3. **Run `analyze`.** Fix every critical and high finding, then run it again. When one finding survives three rounds, write tried / failed / learned into the ledger, then change approach or bring Sami options with your recommendation.
4. **Have a fresh, strong implementation agent prove it works.** Read [references/proof.md](references/proof.md) now and dispatch with its mandate. Fix every defect and re-dispatch until clean. Only a named blocker that Sami explicitly waived exempts this step. While the proof is blocked, report not merge-ready and do not open the PR for review.
5. **Sweep the docs.** Grep READMEs, `docs/`, skills and AGENTS.md files for what the diff made wrong (commands, flags, paths, config keys, behavior) and fix them in this change with `updating-docs`.
6. **Get a review from a fresh, strong reviewer** of the final diff and the proof report, and fix its findings. An urgent fix still gets this review, scoped to the diff. Dispatch that review before you push. Let its verdict gate that fix's merge.
7. **Capture the learning.** A gotcha that cost more than half an hour or contradicted the docs: run `ce-compound mode:non-interactive`. Otherwise say so in one line.
8. **Structure the commits and draft the body before you open or update the PR.** Read [references/commits-and-body.md](references/commits-and-body.md). When the change tightens a contract, changes a producer's schema, publish path or nullability, or makes a render a named person asked for, read [references/plan-and-pr-sections.md](references/plan-and-pr-sections.md) too. Choose commits by reviewability (one commit suits a change of one concern: a single behavior change with its tests and docs) and decide this yourself. Before a history-only rewrite, take the tree fingerprint described there, and compare it after.
9. **Check where the branch may go before you push.**
    - Run `gh api repos/OWNER/REPO --jq .visibility`.
    - Public, and any file, example or history in the branch came from a private repository: push nothing and ask Sami.
    - Never open a PR into our own fork.
    - Open a PR upstream only when the change belongs there (`pr-preflight` decides).
    - Read [references/fork-and-upstream.md](references/fork-and-upstream.md) when `knives repos` lists the repository, when the change touches a fork pin (a dependency pinned to a release of our fork, such as `rev = "release/…"`), before you ship a fork member (a commit on our fork's release base, which a knives release ships), and before you open, push to or post on an upstream PR.
10. **Open or update the PR; default to updating.** Read [references/push-and-open.md](references/push-and-open.md) before the first push, after a red CI run, and before `gh pr create`.
    - One line of work gets one PR. A defect you hit while doing it is part of it: fix it in this PR without asking.
    - Before every push, run the fast local checks outside your agent shell's environment. Push only when they pass, and cite the output lines.
    - A PR without current, observed `## Verification` evidence is not merge-ready: current means at this head, observed means you ran it.

## After the PR exists

- Hand off to `landing-a-pr` at once: it owns the watch and the merge, and a PR nobody watches stalls. Do not run one `gh pr view` and leave.
- Merge a PR that adds or changes a database migration by hand, not by auto-merge, in a repository with no merge-time migration check (`landing-a-pr`'s `repo-merge-rules.md`, "Database migrations"). A merge queue whose merge group runs the migration check is such a check.
