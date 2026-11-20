---
name: opening-a-pr
description: "Use when about to open a pull request or change an open one's head or body, when an open PR's head is not the one its verification ran at, before telling Sami that work is merge-ready, done or blocked on his merge, or when tempted to leave work for later or after the merge."
---

# Opening a PR

Do every step, in order, on every change. A skill named in a step (`analyze`, `updating-docs`, `pr-preflight`, `landing-a-pr`) supplies that step's mechanics and never replaces the step. The diff sets how much evidence a step needs, through the scenarios it affects (step 4); it never sets how many steps you do. "Later" means after the step 4 proof and before human review, never after the merge. Read each step's reference at that step, not from memory: the references change.

Steps 2, 4 and 6 each use a fresh, strong agent: it did not write the change, it runs a strong model rather than a cheap one (`using-subagents`), and it is from a different model family than the author's where your stream's brief permits one. Your stream's brief is the standing instructions your stream's agents work from (the task this session started from names it). It maps each capability to an agent type, for example `oracle` for review and `deep` for proof; a Claude-only brief gets a fresh Claude.

1. **Empty the hardening ledger.** Finish each shortcut, or name its blocker: missing access, or a decision only Sami can make. A follow-up or a GitHub issue does not clear a row. Diff against the target branch and the plan or issue, and finish anything promised but absent. No ledger (no plan with a `## Hardening ledger` section, which `sdd` plans carry)? Write one now: the request, the settled decisions, every shortcut, every part you are unsure you covered. Where the repository has `.github/scripts/checklist_routing.py`, each item of a checklist it requires is a ledger row until it is done.
2. **Have a fresh, strong review agent audit completeness**, because you cannot audit your own scope. Give it the diff, the ledger and the authorization: the plan or issue, else Sami's request and the settled decisions, quoted. Mandate: *"You did not do this work. List everything the authorization promised that this diff does not deliver, and every hack that is still in it."* Fix and re-dispatch until clean.
3. **Run `analyze`.** Fix every critical and high finding, then run it again. When one finding survives three rounds, write tried / failed / learned into the ledger, then change approach or bring Sami options with your recommendation.
4. **Have a fresh, strong implementation agent prove it works.** Read [references/proof.md](references/proof.md) now and dispatch with its mandate. Fix every defect and re-dispatch until clean. Only a named blocker that Sami explicitly waived exempts this step. While the proof is blocked, report not merge-ready and do not open the PR for review.
5. **Sweep the docs.** Grep READMEs, `docs/`, skills and AGENTS.md files for what the diff made wrong (commands, flags, paths, config keys, behavior) and fix them in this change with `updating-docs`.
6. **Get a review from a fresh, strong reviewer** of the final diff and the proof report, and fix its findings. An urgent fix still gets this review, scoped to the diff: dispatch it before you push, and let its verdict gate the merge.
7. **Capture the learning.** A gotcha that cost more than half an hour or contradicted the docs: run `ce-compound mode:non-interactive`. Otherwise say so in one line.
8. **Structure the commits and draft the body before you open or update the PR.** Read [references/commits-and-body.md](references/commits-and-body.md). Choose commits by reviewability (one commit suits a change of one concern: a single behavior change with its tests and docs) and decide this yourself. Before a history-only rewrite, take the tree fingerprint described there, and compare it after.
9. **Check where the branch may go before you push.** Run `gh api repos/OWNER/REPO --jq .visibility`. Public, and any file, example or history in the branch came from a private repository: push nothing and ask Sami. Never open a PR into our own fork, and open one upstream only when the change belongs there (`pr-preflight` decides). Read [references/fork-and-upstream.md](references/fork-and-upstream.md) when `knives repos` lists the repository, when the change touches a fork pin, before you ship a fork member, and before you open, push to or post on an upstream PR.
10. **Open or update the PR; default to updating.** Read [references/push-and-open.md](references/push-and-open.md) before the first push, after a red CI run, and before `gh pr create`.
    - One line of work gets one PR. A defect you hit while doing it is part of it: fix it in this PR without asking.
    - Before every push, run the fast local checks outside your agent shell's environment. Push only when they pass, and cite the output lines.
    - A PR without current, observed `## Verification` evidence is not merge-ready: current means at this head, observed means you ran it.

## The plan, spec and PR also carry

- **A contract change census**, when the change tightens a contract: it adds a refusal, requires, removes or renames a field, or changes a process or package signature. The PR, and a plan under `sdd` before implementation, carries `## Contract change census` with three parts:
  1. The search commands and scope. Trace the data, payload or file shape and the endpoint across this repository and every other repository that produces or consumes it: search its owner with `gh search code '<field>' --owner <owner>`, and add each consumer you know under another owner. Cover every language, infrastructure handlers, doc templates, skills and recorded test fixtures (saved payloads and golden files). Name-only or importer searches miss callers that rebuild the value.
  2. Every hit and its disposition: updated here / exercised by `<test>` / unaffected because `<reason>`. The reviewer reruns the search. Minutes before merge, rerun it against main's tip; update new hits, or hold the merge until you coordinate with the PR that added them.
  3. One rollout line:
     - `Rollout: warn-first; refuses in <follow-up PR or issue>`: an actionable warning, exit 0, and a record of what it would refuse. The follow-up enforces only after those records, from real traffic, show zero would-refuse entries, and cites the count.
     - `Rollout: immediate refusal; security: <what the old input let someone do>`: only for a vulnerability, not attribution, bookkeeping or data quality.
     - Inputs saved outside this repository always get warn-first, whatever the census found.
- **The downstream delta** of a change to a producer's schema, publish path or nullability: name each downstream surface and what it will show differently, and have the consumer's owner review that delta. With no consumer found, paste the search you ran and its empty output into the census. This needs no approval from Sami.
- **The audience of a requested render**: when a named person asked for a render, record that person and the channel where they will see it live. You need no reply.

## After the PR exists

- Hand off to `landing-a-pr` at once: it owns the watch and the merge, and a PR nobody watches stalls. Do not run one `gh pr view` and leave.
- A PR that adds or changes a database migration is merged by hand, not by auto-merge, in a repository with no merge-time migration check (`landing-a-pr`'s `repo-merge-rules.md`, "Database migrations"). A merge queue whose merge group runs the migration check is such a check.
- Before you say done or merge-ready, restate the original goal and point to the step 4 evidence for it.
