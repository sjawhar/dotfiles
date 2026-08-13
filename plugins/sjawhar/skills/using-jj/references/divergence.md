# Covers identifying and resolving divergent jj commits without damaging remote history.

## Resolving divergence

Divergence means one change ID has two or more commits. It shows up whenever you rewrite a change (`describe`, `rebase`, `abandon` of an ancestor) while another reference still points at the old commit. Finish the cleanup — but treat it as bookkeeping, not a hazard.

**A local abandon cannot harm the remote.** `jj abandon` edits your local view only: it pushes nothing, origin's refs keep pointing exactly where they did, and a later `jj git fetch` re-materializes whatever you dropped. A lockfile pinning a branch + SHA resolves against origin, so it is unaffected. The `immutable` marker is a guardrail against rewriting *your local copy* of published history — it is not protection for the remote.

Resolution, in order:

1. **Forget the reference you no longer need, then abandon the copy you don't want:** `jj bookmark forget <name>`, then `jj abandon <commit-id>`. Address commits by **commit ID, not change ID** — a divergent change ID is ambiguous; use `jj log -r 'change_id(abc)'` to list every copy.
2. **If jj refuses because the commit is immutable,** pass `--ignore-immutable`. (Tracking the bookmark and forgetting it again also clears the immutability, but the flag is the direct tool.)
3. **If an empty, bookmark-less commit reappears every time you abandon it,** it is another workspace's working copy, not cruft. Check `jj workspace list`. Retire it with `jj workspace forget <name>`; stock jj only stops tracking the workspace and leaves its directory alone. Abandoning its `@` only makes jj recreate one.
4. **Before abandoning anything non-empty, check it is not unsalvaged work.** Read its diff and confirm the content exists elsewhere (a current branch, or a branch on origin). Empty commits and empty octopus merges for superseded releases are always safe.
5. **Verify you destroyed nothing:** capture `git ls-remote --heads origin` before and after, diff the two, and confirm every open PR head commit still resolves.

Rewriting a commit that an octopus merge or a published release pins is fine. The merge is rewritten locally and its bookmark moves off the remote's commit; forget the superseded bookmark and abandon the leftover. Never "fix" local divergence by deleting branches from the remote — that destroys published history to tidy a local view.
