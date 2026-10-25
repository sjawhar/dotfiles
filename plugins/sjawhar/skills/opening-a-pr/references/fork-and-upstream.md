# Step 10: fork members and upstream PRs

## Placement

- Our org forks hold branches and releases and never receive PRs. A warranted PR targets upstream.
- Most changes are fork members: commits on the fork release branch that the current release is building, with no upstream PR.
- Not upstream material: a fork-specific workaround, an infra knob only we use, an unproven fix.
- Upstream material: a defect any user of the library would hit, with a reproduction or red-to-green test, verified on a production-like surface.
- Upstream PRs land under Sami's GitHub account, so the maintainer reads them as his: every claim in the body must be one you ran. They need no permission gate or approval step.

## Shipping a fork member

The default for a hawk or inspect bug:

1. Make one signed commit on the release base, push it to the fork remote, and `knives notch` it.
2. Send the tip and red-to-green evidence to the release-claim holder, who includes it in the release in hand and publishes. When every consumer pin of that release is frozen on a revision (`rev = "release/…"`), cut the next dated name first. With no claim held, do this yourself.
3. The consumer pins the published release name and deploys.

When the placement rules above make the change upstream material, open the upstream PR alongside. It gates none of this. Never wait for upstream review to use a fork change.

## Upstream PRs

- Walk `pr-preflight` (`knives preflight`) before `gh pr create`. Follow the upstream contribution policy in its AGENTS.md or contributing guide.
- An open upstream PR is the session's under `maintaining-fork-pr`: reviews, body updates, rebases, the close decision. Answer maintainer comments yourself; hold one for Sami only when it asks for a decision only he can make.
