# Watchers and post-merge delivery

Read before you arm a detector, diagnose a silent watcher or check delivery. Use `ce-babysit-pr` and the existing supervisor.

## The watcher

- Events wake the owner. A fresh snapshot says what is true.
- Do other work while you wait. Run the watcher under supervision, with a real wake route.
- Read its first poll. Silence fits a missing command, a broken parser, an expired token and no event alike. Before you act on its exit, read the run's own status.
- List the real job names before you write a filter. Match the full nested name, never a substring such as "deploy". Record the exact job and conclusion you wait for.
- Single-quote jq programs or use a file. An empty or failed parse is a loud failure. Reconcile expected labels against partial output. Keep stderr out of compared values.
- Call `gh` fresh for each observation so the token refreshes. Never capture one token for a long loop. Check run-list limits and event filters before you conclude that all runs for a SHA finished.
- A long watch stream can stop cleanly. When it underpins proof, reconcile its object IDs with independent reads or driver actions. Resume from the last resource version where supported.

## The carrying run

- Find the run whose head contains the merge, by ancestry. After you fetch both objects, `jj log -r '<merge-sha> & ::<run-head>'` must return the merge. Read the required job's state.
- Pin that run for the whole wait. Never follow "the newest live run" each tick.
- If the pinned run is cancelled, superseded or gone, re-resolve and check ancestry again. When a queue coalesces or replaces runs, account for every candidate.
- Cancellation alone is neither a production failure nor proof of non-delivery. Read a coalescing queue's semantics before you blame a cancellation. Do not apply them to other workflows.
- Diagnose a failed run at the named job and step, folded outcomes included.

## The artifact in production

- After the production apply, identify the deployed image or version of the component this PR changed.
- For installed software, check the version the consumer runs. An install the authorized change needs is part of the work. Use a head-specific artifact or behavior, never a guessed string in a minified bundle.
- Zero-write probe: an atomic batch whose first step the new version rejects and whose second step always fails on the old version. Check the ordering and predicted errors first.

## After the squash

- A deleted branch or missing pre-squash SHA does not show whether work merged. Read the destination's content (`jj file show -r <destination> <path>`) before you redo work.
- The GitHub PR-head ref can keep PR commits reachable. Check it before you rely on it. Cite intermediate evidence by SHA and PR number.
- Read the merge state and message from GitHub, not a command's exit or an armed auto-merge:

```sh
gh api repos/<owner>/<repo>/pulls/<N> --jq '{merged, merge_commit_sha}'
gh api repos/<owner>/<repo>/commits/<merge-sha> --jq .commit.message
```

- Squash defaults can join old subjects, retracted claims too. Reread the title and body against the final head before the packet, and the merged message after. Never rewrite merged history.
- Name the SHA each figure came from.
