# Shared-store safety

## Before a rewrite

1. Run this as its own command and read the IDs it prints:

   ```bash
   jj log -r '(descendants(roots(main@origin..@)) ~ ::@) & ~empty()'
   ```

   Each commit it prints is someone else's work stacked on yours. Do not chain it into the rewrite: a chained check prints and the rewrite runs anyway. `~empty()` leaves out other workspaces' empty working copies.
2. If it printed anything, get the owners' agreement, or put your chain on a new base without moving theirs: `jj duplicate <your range> -o main@origin`.
3. On a branch another session may have stacked on, make each further change a child (`jj new <sha>`), not an amend.

- `rebase -b @` and `-s <root>` move every chain stacked on yours. `-r <range>` re-parents the stacked chain onto your old base, so its tree loses your changes. `abandon` rebases descendants onto the abandoned commit's parent.
- A broad rebase, or a rebase of one revision beneath a many-parent release merge or a running session's working copy, moves far more than it names. A release merge is a descendant too: coordinate with its owner, who moves it with `knives release rebase`.
- Owning a change ID does not make an abandon safe. After a squash merge, delete your bookmark, forget your workspace and leave the merged commits. Abandon only when `jj log -r 'descendants(<id>) ~ <id>'` is empty.
- A probe that makes commits (`squash`, `commit`, `new`) is cleaned up by abandoning the change IDs it made, by ID; a clean `jj status` does not clean it. Prefer a throwaway repository in `mktemp -d`.
- Before a restore wider than your own explicit paths, copy the current files aside.

## Owners

- The `Omp-Session` trailer names a commit's session (`session-attribution` skill). Without one, `jj op log | grep <bookmark>` names the workspace that pushed it; it can be your own forgotten one. For a stray workspace nobody claims, compare its working-copy commit time with each candidate's workspace-add time in `jj op log`.
- A conflicted local bookmark warns in every `jj status`. Clear your own at once. For another owner's, check the commit is still visible (`jj log -r <commit>`), tell the owner what it pointed at, then `jj bookmark forget` it. If the owner does not answer, forget it and write in your report that you could not reach them. To check whether it is on main, compare subject or content: `git merge-base --is-ancestor` calls every squash-merged commit absent.

## Immutable refusals

- In an agent session, `immutable_heads()` is `builtin_immutable_heads()` plus a session guard that makes other sessions' non-empty unpushed commits immutable. The guard exempts commits with your session's trailer, `present(@)`, empty commits and undescribed commits that are no workspace's working copy. Print the live rules with `jj config get 'revset-aliases."immutable_heads()"'` and `jj config get 'revset-aliases."builtin_immutable_heads()"'`.
- Use `--ignore-immutable` only against the session guard, for a commit your own session owns. An exemption is not permission to clean up: an empty or undescribed commit can hold recovery work.
- A builtin pin freezes a head and its ancestors even if you never pushed it (a `<number>@pr` ref is an untracked remote bookmark). Never `--ignore-immutable` it: run `jj new` and work on the fresh child.
- In a knives-managed fork (one that `knives repos` lists), an immutable refusal on a rebase is usually a stale pin: a superseded release ref, or another fork's PR head. `knives start` states the repository's `immutable_heads()` rule where none is stated, and `knives status` reports it as `immutable-heads-rule`.
- There, `jj squash` refusing to "rewrite N immutable commits" protects release members: the branches whose tips a knives release merges (`knives release members <release>` lists them). Do not override it or work around it with `jj duplicate`: release members are matched by change ID. Move many members with `knives release rebase`, not per-branch jj.
- A refusal exits 1 with the error on stderr only. Under `2>/dev/null` a script carries on against an unchanged tree, so keep stderr and check the change applied.
