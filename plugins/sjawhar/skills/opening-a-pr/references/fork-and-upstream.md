# Step 10: fork members and upstream PRs

## Placement

- Our org forks hold branches and releases and never receive PRs. A warranted PR targets upstream.
- Most changes are fork members: commits on the fork release branch that the current release is building, with no upstream PR.
- Not upstream material: a fork-specific workaround, an infra knob only we use, an unproven fix.
- Upstream material: a defect any user of the library would hit, with a reproduction or red-to-green test, verified on a production-like surface.
- Upstream PRs land under Sami's GitHub account, so the maintainer reads them as his: every claim in the body must be one you ran. Answering a maintainer's review needs no approval from him, not even for a force-push that replaces the PR's commits as the maintainers asked; a description does, as the voice bullet below says. Every change to one passes the gate under "Upstream PRs".

## Shipping a fork member

The default for a hawk or inspect bug:

1. Make one signed commit on the release base, push it to the fork remote, and `knives notch` it.
2. Send the tip and red-to-green evidence to the release-claim holder, who includes it in the release in hand and publishes. When every consumer pin of that release is frozen on a revision (`rev = "release/…"`), cut the next dated name first. With no claim held, do this yourself.
3. The consumer pins the published release name and deploys.

When the placement rules above make the change upstream material, open the upstream PR alongside. It gates none of this. Never wait for upstream review to use a fork change. In the Rocq and Lean forks, `maintaining-prover-forks` sets the timing instead: a bug fix's upstream PR, and the bug report that goes with it, wait until a pushed release carries the fix.

## Upstream PRs

- Walk `pr-preflight` (`knives preflight`) before `gh pr create`. Follow the upstream contribution policy in its AGENTS.md or contributing guide.
- An open upstream PR is the session's under `maintaining-fork-pr`: reviews, body updates (a description rewrite goes to him first, see the voice bullet), rebases, the close decision. Answer maintainer comments yourself; hold one for Sami only when it asks for a decision only he can make.
- **Before you open an upstream PR, push to one or post on one** (code, body, comment or reply), it passes this gate, because a weak PR burdens the maintainers, harms our reputation, and leaves us maintaining code we cannot upstream (dispatch://BET-122/ask/b28e2fb4):
  1. The reviewers `sdd` step 7 names, every verdict naming the final commit: `ce-simplify-code` first for a runtime change, then `reviewer`, `thermonuclear-deep-review` and `thermonuclear-code-quality`. For a post with no new commit, they review the draft text against the diff at the PR head. `maintaining-fork-pr` step 6 runs the last three on its candidate, so one round serves both.
  2. `pr-preflight` for a new PR, and the rest of the `maintaining-fork-pr` process for an open one.
  3. The project's conventions: its contributing guide, and the size and form of its recently merged PRs (see "Match the project" below).
  4. The feedback its maintainers would predictably give: read every maintainer comment on our PRs to that project, and make the change answer it before they ask.

  For rocq-prover/rocq, `maintaining-prover-forks` adds its own rules. The voice of the text is in `speaking-as-sami`.

## The upstream PR description

A maintainer reads the description as Sami's own writing, and a long one reads as machine output. On rocq-prover/rocq#22610 a maintainer answered one of ours with "Unreadable llm verbosity for a feature I'm not sure we even want." Sami had asked several times for descriptions to be cut down before they go out (dispatch://BET-108/ask/d4db8dea, 2026-10-26). So, before an upstream PR opens and after every body edit:

- **Match the project, not this skill.** Read five recently merged PRs in the target repository and write to their length and form. Our five rocq-prover/rocq descriptions that drew no complaint run 71 to 154 words; the one that did ran 964. The 150 to 250 word aim in [commits-and-body.md](commits-and-body.md) is for our own repositories; upstream, the project's median wins when it is shorter.
- **Say only what a reviewer needs to decide.** What the change does, why a user of the project wants it, what stays the same without it, and where the tests are. Move every other detail into the documentation or tests the PR adds, where a reader can choose to read it. Name nothing internal: no grade farm, no run IDs, no fork names.
- **Ask before you build, when the project does.** If the project's guide or its maintainers' past comments prefer an issue or a forum post first, open that first, and open the PR only once a maintainer has said the feature is wanted.
- **A fresh reader checks it before it goes out.** Give the description, and nothing else, to a fresh read-only agent told it is a maintainer of that project with no context. Fix every sentence it calls verbose, unclear, or written like a machine. Then post.
- **The same holds for what the PR adds.** A maintainer reads the documentation, changelog entry and test comments in the same sitting. Each documentation entry matches its neighbours' length and form; rationale and advice to downstream programs stay out.
- **Open it from a fork maintainers can push to.** GitHub's "Allow edits from maintainers" works only on a user-owned fork, so open upstream PRs from `sjawhar/<repo>` (for Rocq, `sjawhar/rocq`), not an organisation's fork. On rocq-prover/rocq#22608 a maintainer wrote the fix himself and could not push it to our organisation's fork.
- **Write in Sami's first-person voice, with one line saying Claude helped.** The wording and its source are in `speaking-as-sami`. Replies to reviewer comments, and the code changes that answer them, need no ask: a change that passes the gate above goes out (dispatch://BET-122/ask/b28e2fb4). A new PR's description, or a rewrite of an open PR's description, goes to him first, all of a session's texts in one ask: he chose "your voice, approved by you" (dispatch://BET-64/ask/7f10d8e8). Upstream commits keep their `Omp-Session` trailer (same answer); never ask him about it again.
