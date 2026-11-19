# Watchers

Read before you start a watcher or diagnose a silent one. Watching a deploy after the merge is `post-merge`'s.

- One watcher per PR across all sessions; watching three PRs is one loop over three. Do other work while it runs, as a background job whose output or exit reaches you as an Envoy message or a tool follow-up.
- Do not run a bare `gh run watch` or `gh pr checks --watch`: they poll every 3 and 10 seconds, and every agent on the organisation shares one App installation's REST budget. Pass `--interval 120` or longer. On a rate-limit error, back off; do not retry sooner.
- Read its first poll. Silence fits a missing command, a broken parser, an expired token and no event alike. Before you act on its exit, read the run's own status.
- A review someone acknowledged but has not posted keeps the PR not ready, green CI included. Events wake you; a fresh snapshot says what is true.
- Call `gh` fresh for each observation so the token refreshes; do not capture one token for a long loop. Check run-list limits and event filters before you conclude every run for a sha finished.
- A long watch stream can stop cleanly. When it underpins proof, reconcile its object IDs with independent reads, and resume from the last resource version where supported.
- Count and kill a watcher by its parent. A wrapped watch (`knives gh -- pr checks … --watch`) is a wrapper and a `gh` child, and a killed child is re-spawned by the wrapper. Kill the wrapper by pid, then confirm the child is gone.
- After a two-call `gh` operation (`pr ready --undo` then `pr ready`, a label added then removed, auto-merge disabled then enabled), one half can land while the other fails. Read the field you meant to change (`gh pr view <N> --json isDraft,labels,reviewDecision,autoMergeRequest`), not the last error.
