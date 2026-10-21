# Step 10: fork members and upstream PRs

## Placement

- Our org forks hold branches and releases and never receive PRs. A warranted PR targets upstream.
- Most changes are fork members in the release in hand, with no upstream PR.
- Not upstream material: a fork-specific workaround, an infra knob only we use, an unproven fix.
- Upstream material: a defect any user of the library would hit, with a reproduction or red-to-green test, verified on a production-like surface.
- Upstream PRs land under Sami's GitHub identity: a high quality bar, but no permission gate or approval step.

## Shipping a fork member

The default for a hawk or inspect bug:

1. Make one signed commit on the release base, push it to the fork remote, and `knives notch` it.
2. Send the tip and red-to-green evidence to the release-claim holder, who includes it in the release in hand and publishes. When every consumer pin of that release is frozen on a revision (`rev = "release/…"`), cut the next dated name first. With no claim held, do this yourself.
3. The consumer pins the published release name and deploys.

An UPSTREAM verdict opens the upstream PR alongside. It gates none of this. Never wait for upstream review to use a fork change.

## Upstream PRs

- Walk `pr-preflight` (`knives preflight`) before `gh pr create`. Follow the upstream contribution policy in its AGENTS.md or contributing guide.
- An open upstream PR is the session's under `maintaining-fork-pr`: reviews, body updates, rebases, the close decision. Never leave maintainer feedback waiting for a human.
