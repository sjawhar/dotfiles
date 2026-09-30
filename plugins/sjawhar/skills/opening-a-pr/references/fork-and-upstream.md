# Fork members and upstream PRs

Read this from step 10 of `opening-a-pr` when the repository is a knives-managed fork (`knives
repos` lists it), when a change touches a fork pin, or before any upstream PR. The step's rules
are in `SKILL.md`; this file carries the placement test and the mechanics.

## Is it upstream material?

Most of our changes are fork members that go into the release in hand and never need an upstream
PR. A fork-specific workaround, an infra knob only we use, or an unproven fix is NOT upstream
material. What IS: a defect any user of the library would hit, fixed with evidence (a reproduction
or red-to-green test) and verified on a production-like surface (step 4).

Every upstream PR lands under Sami's personal GitHub identity, so the bar is his reputation with
METR/AISI. That is a quality bar, not a permission gate: he is not in the loop per interaction, and
there is no grant, env var, or approval step. Do not open an upstream PR without loading the
`maintaining-inspect` skill (and `fork-work`/`using-knives` for the fork mechanics) and thinking
through, in writing, in the PR body or the `knives notch`, whether the change actually needs to go
upstream at all.

## Shipping a fork member

The default for a hawk/inspect bug is a fork member:

1. One signed commit on the release base, pushed to the fork remote, and `knives notch`ed.
2. The tip and red-to-green evidence go to whoever holds the release claim. They include it in the
   release in hand, cutting the next dated name first, right then, when every consumer pin of that
   release is frozen on a revision (as the consumer's `rev = "release/…"` pins are), and publish.
   With no claim held, you do the same yourself.
3. The consumer pins the published release name and deploys.

Using a fork change takes exactly as long as making it. The upstream PR, when the placement
verdict is UPSTREAM, opens alongside and gates none of that; never wait for upstream review before
using a fork change.

## Opening and owning an upstream PR

Walk the `pr-preflight` skill (`knives preflight`) before any `gh pr create`, and follow the
upstream repository's own contribution policy (read its AGENTS.md or contributing guide for
requirements such as an accepted issue for non-trivial PRs or an agent-review disclosure). Once an
upstream PR is open, its whole lifecycle is the session's under `maintaining-fork-pr`: review
rounds, body updates, rebases and the close decision. Never leave maintainer feedback unanswered
waiting for a human.
