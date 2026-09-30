# Watchers and post-merge delivery

Read before arming a detector, diagnosing a silent watcher or verifying delivery.
Use `ce-babysit-pr` and the existing supervisor, not a foreground sleep loop.

## Wake on actionable state

Subscribe through Envoy to
`notifications.github.<owner>.<repo>.pr.<N>.>` and arm `pr-snapshot watch` as the
background truth detector. Events wake the owner; a new snapshot establishes
what is true. Keep the babysitter's claim/act/confirm dedup and settle window.

A detector can wake for every check completion. Re-arm without a full tick while
checks are still queued/running or the expected aggregate is not present. Tick
when that graph settles, on review events or on a push. The review-still-expected
guard remains independent of CI: an acknowledged pending review holds readiness
until it actually completes.

Do useful independent work while waiting. A watcher must run under supervision
and have a real wake route; the user's next message is not that route. Never
block the foreground on `gh pr checks --watch`, `gh run watch` or a sleep loop.

## Establish that the watcher works

Read its first poll and output after start. Silence is compatible with missing
commands, a broken parser, an expired token or no event; it proves none of them.
Before acting on exit, read the run's own current status. A watcher exiting
nonzero does not establish that the remote run failed, and a zero exit does not
establish a complete record.

List actual job names before writing a filter. Match the full nested name,
not a substring such as “deploy”, which can select a preflight instead of the
production apply. A nested reusable workflow can add another name segment.
Record the exact job and conclusion the watcher is waiting for.

Use single-quoted jq/jaq or a program file. Empty/failed parses are loud failures,
not “no news”. Partial parses need an expected-label/count reconciliation too.
Do not merge stderr notices into the value being compared.

Call `gh` afresh for each point observation so routed tokens refresh; do not
capture one token for a long-lived loop. A single blocking process can outlive
its credential. Check run-list limits and event filters before concluding all
runs for a SHA completed; bot workflow events can fill a default page while the
real check is still running.

Long watch streams can end cleanly without an error. When their output underpins
a proof, reconcile object IDs against independent reads or driver actions. Resume
from the last resource version where supported rather than trusting one endless
watch. A stopped stream looks exactly like a world in which nothing happened.

For CI diagnosis, complete endpoint reads and mid-run job logs, use
[CI observation](ci-observation.md).

## Select the carrying run, then pin it

Before waiting, find the run whose head contains the merge. Query ancestry, not
its title or timestamp: `jj log -r '<merge-sha> & ::<run-head>'` must return the
merge after those objects are fetched. Read the required job's actual state.

Once a live run is carrying the commit, pin its ID throughout the wait. Do not
select “newest live run” on every tick: that can discard a successful answer on
the older run and follow another one forever.

If the pinned run is cancelled, superseded or gone, re-resolve to the current
non-cancelled carrying run and verify ancestry again. Account for all candidate
runs when a queue coalesces or replaces them; do not inspect only the newest one
and miss an earlier actual apply.

Cancellation is not by itself a production failure or proof of non-delivery.
In a coalescing deployment queue, a waiting run can be replaced while another
applies, and its commits can be included in the next run. Read that queue's own
semantics before attributing delay to a cancellation; do not generalize those
semantics to every workflow cancellation.

A failed run is diagnosed at the named job/step, including folded outcomes. If
our change caused it, the author owns the repair and next delivery. Track the
work as in verification until the production path is proven, not done at merge.

## Verify the artifact through the real user path

Run `post-merge`. After the actual production apply/publish step, identify the
service's deployed image, package version or installed plugin version. A green
chain can update one component while skipping the component this PR changes.

Drive the changed production behavior through the user's own access path. A
staging pass does not establish production success, especially when production
has resources or identities staging does not. Record the observation on the PR.

For installed software, verify the version the consumer actually runs, not just
the released version. If installation is required to deliver the authorized
change, it is part of the work. Use a head-specific artifact or behavior that
differs from the old version; a guessed string in a minified bundle is not proof
of deployment or absence.

For zero-write safety probes, an atomic operation can discriminate versions
without leaving changes: an operation the new version rejects first, followed
by one that always fails and rolls the batch back on the old version. Establish
the actual ordering and predicted errors before using that shape; a find that
fails before the new guard executes proves nothing about the guard.

## Post-squash citations and messages

A deleted branch name or absent pre-squash SHA does not tell whether the work
reached the destination. Ask about the destination's content or behavior at its
current revision. Use `jj file show -r <destination> <path>` and the relevant
history/behavior, rather than re-staffing work because a temporary ref vanished.

PR commits may remain reachable through GitHub's retained PR-head ref after a
squash merge and branch deletion. Verify that ref before relying on it; cite
intermediate evidence with commit SHA and PR number, not the deleted branch.
A positive control proves a remote read worked, not that absence of a temporary
ref answers whether the content merged.

Read the actual merge state and merged message from GitHub, not merely a merge
command's exit. The owner still does not perform the merge; these are read-back
checks after the organizer's action.

```sh
gh api repos/<owner>/<repo>/pulls/<N> --jq '{merged, merge_commit_sha}'
gh api repos/<owner>/<repo>/commits/<merge-sha> --jq .commit.message
```

A repository's squash defaults may concatenate old subjects, including claims
later retracted. Re-read the PR title/body against the final head before the
packet and the resulting commit message afterward. Do not rewrite merged
history to correct it. Name the SHA each figure was measured at: “at the head”
changes meaning whenever the bookmark moves.
