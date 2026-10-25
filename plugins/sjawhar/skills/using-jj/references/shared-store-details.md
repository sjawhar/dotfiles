# Shared-store safety

All workspaces share one commit store and one operation log. Snapshots that read-like commands take also change that history.

## Scope every mutation

1. `jj undo`, `jj op restore` and `jj op revert` rewind every workspace's working-copy commit. A workspace whose session ended is not idle. Never run them from anywhere, and never to repair another session's mistake. A hint that names `jj op restore` describes what an operation could undo. It is not a command to run.
2. `jj op revert <your op>` is not safe either: if another workspace edited a commit your operation carried, their change and bookmark go divergent and their workspace stale. An operation's description does not tell you what it touched. Repair forward, for example by recreating a bookmark.
3. A bare `jj restore`, `jj abandon` or `jj new` in a shared checkout takes other sessions' uncommitted work. `divergent()`, `mine()`, `empty()`, `visible_heads() & ~immutable()`, `main@origin..` and `all:` match every session's commits. Name your change IDs one by one.
4. `jj restore --from @- <explicit paths>` in your own workspace is safe. Use it, for example, to show that a test fails without your change. Before any restore beyond your own explicit paths, copy the current files aside.
5. A probe that makes commits (`squash`, `commit`, `new`) is cleaned up by its commits, not by a clean `jj status`. Prefer a throwaway repo in `mktemp -d`. In the real store, list the change IDs it made and abandon those by ID.
6. After a failure, read the state before each correction. A second correction without a fresh read compounds the damage.

## Every rewrite moves what is stacked on it

- jj rebases descendants automatically, so amending, squashing into, redescribing (`Rebased 1 descendant commits`) or abandoning a commit moves whatever another session stacked on it. Their workspaces go stale, their next snapshots diverge, and uncommitted edits can drop.
- `rebase -b @` and `-s <root>` move every chain stacked on yours. `-r <range>` is worse: it re-parents the stacked chain onto your old base and deletes your files from its tree. `abandon` rebases descendants onto the abandoned commit's parent.
- A broad rebase, or one revision beneath a many-parent release merge or a running session's working copy, moves far more than it names. A release merge is a descendant: coordinate with its owner, who moves it with `knives release rebase`.
- Before any of these in a shared store, run this as its own command and read the IDs:

  ```bash
  jj log -r '(descendants(roots(main@origin..@)) ~ ::@) & ~empty()'
  ```

  Do not chain it into the rewrite. A chained check prints and the rewrite runs anyway. Keep `~empty()`: other workspaces' empty working copies on your head count. Output is someone else's work. No output can mean a broken check (an unquoted `-T` prints nothing), so print IDs.
- To put your chain on a new base without moving what is stacked on it: `jj duplicate <your range> -d main@origin`.
- On a branch another session may have stacked on, make each further change a child: `jj new <sha>` before you edit.
- Owning the change ID does not make an abandon safe. After a squash merge, delete the bookmark, forget your workspace and leave the merged commits. Abandon only when `jj log -r "descendants(<id>) ~ <id>"` is empty. A cross-session abandon leaves the other workspace stale. `update-stale` keeps its edits ([workspaces](workspaces.md)).
- A moved base alone is not a reason to rewrite a reviewed branch.

## Owners

- Before you treat a commit as another session's, find the owner. Without a session trailer, `jj op log | grep <bookmark>` names the pushing workspace. It can be your own forgotten one. For a stray workspace nobody claims, compare its working-copy commit time with each candidate's workspace-add time in `jj op log`.
- A conflicted local bookmark warns in every `jj status`. Clear yours at once. For another owner's, check that the commit is still visible (`jj log -r <commit>`). Check by subject or content whether it is on main. `git merge-base --is-ancestor` calls every squash-merged commit absent. Tell the owner what it pointed at, then forget it. If the owner does not answer, forget it and write in your report that you could not reach them.

## Immutable refusals

- Find the pin before you diagnose: `jj log -r 'immutable_heads() & descendants(<rev>)'`. Ownership alone does not decide mutability.
- The session guard is the revset agent sessions add to `immutable_heads()`: it makes other sessions' non-empty unpushed commits immutable. So `immutable_heads()` is `builtin_immutable_heads() | (session guard)`. The exemptions (your trailer, `present(@)`, empty commits, undescribed orphan snapshots) apply only to the session guard. Read the live rule: `jj config get 'revset-aliases."builtin_immutable_heads()"'`.
- `--ignore-immutable` is allowed only against the session guard, for a commit your own session owns. An exemption is not permission to clean up. An empty or undescribed commit can hold recovery work.
- Builtin pins (trunk, tags, `untracked_remote_bookmarks()` such as a `<number>@pr` ref) freeze a head and its ancestors even if you never pushed it. Pushed and immutable are independent. Your commit under another owner's commit is protected through ancestry. A human shell without the session guard keeps the builtin protections. Remedy: `jj new`, then mutate the fresh child. Never `--ignore-immutable` a builtin refusal.
- In a fork, an immutable rebase is usually a stale pin (a superseded release ref, another fork's PR head). `jj squash` refusing "would rewrite N immutable commits" is the guard that matters. Never override it, and never substitute `jj duplicate`: release members are matched by change ID. `knives start` sets the repository's rule where none is stated, and `knives status` reports it as `immutable-heads-rule`. Move many members with `knives release rebase`, never per-branch jj.
- A refusal exits 1 with empty stdout and the error on stderr. Under `2>/dev/null` a script carries on against an unchanged tree. Keep stderr, and prove the mutation applied (grep for a string only the new version has) before you trust a result.
