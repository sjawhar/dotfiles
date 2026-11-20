# Step 9: fork members and upstream PRs

## Placement

- Placement, the challenge before a branch, shipping a fork member through a knives release, and opening its upstream PR alongside are in knives' `fork-work` and `pr-preflight`. For the Rocq and Lean forks, `maintaining-prover-forks` also sets when an upstream PR and its bug report open.
- Never open a PR into our own fork: our organization's forks hold branches and releases only.
- A fork-carried change goes upstream, becomes an extension, or is not worth doing. Carrying it in the fork indefinitely is not a fourth option: give every fork-carried change that pass, and open the PR for what survives it.
- Never wait for upstream review to use a fork change.

## Upstream PRs

- Upstream PRs land under Sami's GitHub account, and the maintainer reads them as his: every claim in one must be one you ran.
- Walk `pr-preflight` (`knives preflight`) before `gh pr create`, and follow the project's AGENTS.md or contributing guide.
- An open upstream PR is the session's own under `maintaining-fork-pr`: reviews, body updates, rebases, the close decision. Answer maintainer comments yourself; hold one for Sami only when it asks for a decision only he can make.
- Before you open an upstream PR, push to one, or post on one (code, body, comment or reply), it passes this gate, because a weak PR burdens the maintainers and leaves us maintaining code we cannot upstream:
  1. Reviewers, every verdict naming the final commit: `ce-simplify-code` first for a runtime change, then `reviewer`, `thermonuclear-deep-review` and `thermonuclear-code-quality`. For a post with no new commit, they review the draft text against the diff at the PR head. On an open PR, the review round of `maintaining-fork-pr` runs the last three and serves for both.
  2. `pr-preflight` for a new PR; the rest of `maintaining-fork-pr` for an open one.
  3. The project's conventions: its contributing guide, and the size and form of its recently merged PRs.
  4. The feedback its maintainers would predictably give: read every maintainer comment on our PRs to that project, and make the change answer it before they ask.
- A reply to maintainer review, and the code change or push that answers it (a force-push that replaces the commits as they asked included), goes out once it passes the gate, with no ask. A new PR's description, or a rewrite of an open PR's description, goes to Sami first, all of a session's texts in one ask (`outside-message` has the ask's form).
- Upstream commits keep their `Omp-Session` trailer. Do not ask Sami whether to keep it.

## The upstream PR description

Before an upstream PR opens and after every body edit:

- Follow the procedure in the `pr-description.md` reference of `maintaining-prover-forks`, all five steps, for every upstream project, not only the provers: size and form from the project's merged PRs; what the change does, why it is correct, what it costs; the rest in tests and code comments; a fresh reader that only deletes; no line about who or what wrote the text. The contributor sentence below is the one exception to step 5. For rocq-prover/rocq, follow the "Rocq upstream PR" rules of `maintaining-prover-forks` too. This replaces "The body" in [commits-and-body.md](commits-and-body.md). Name nothing internal: no grade farm, run IDs or fork names.
- Write in Sami's first person (`speaking-as-sami`), with no other line about who or what wrote the text. His standard contributor sentence goes in every upstream PR and needs no ask; use it unchanged unless he asks for a change:
  - short: `My agent found this while analyzing our fleet's transcripts.`
  - fuller, preferred for a change larger than the recently merged PRs you read for step 1 of `pr-description.md`, or one from a fleet-scale observation, since its disclaimer is true and a maintainer needs it: `my agents found this while we were running hundreds of agents at once; I have not personally reviewed this myself, it came out of analysing live runs on my own machine.`
- Why the change is worth making goes in the body's own sections, never as an incident story in Sami's first person.
- Ask before you build when the project does: if its guide or its maintainers' past comments prefer an issue or forum post first, open that, and open the PR only once a maintainer says the feature is wanted.
- The documentation, changelog entry and test comments the PR adds follow the same rules, because a maintainer reads them with the description: each matches its neighbours' length and form, and rationale and advice to downstream programs stay out.
- Open it from `sjawhar/<repo>` (for Rocq, `sjawhar/rocq`), not an organization's fork: GitHub's "Allow edits from maintainers" works only on a user-owned fork.
