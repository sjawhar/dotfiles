# Shared-store safety and workspace ownership

Read from the matching step of the using-jj skill. Git commands here are read-only
diagnostics or a named exception; version-control changes use jj.

## Scope every mutation, worst first

1. **`jj undo`, `jj op restore` and `jj op revert` are repository-global.** The operation log
   belongs to the repository, not the workspace, so an undo from any workspace rewinds every
   other workspace's working-copy commit too. A workspace whose session ended is not idle. Never
   run them, from anywhere, including your own workspace, and never to repair someone else's
   cross-session mistake: the repair is the same forbidden class as the mistake. Recovery hints
   that name `jj op restore` describe what an operation could undo, not a command to run here.
2. **`jj op revert <your op id>` is not a safe undo either.** If another workspace edited a
   commit your operation carried (for example, a rebase), reverting it makes their change and
   bookmark divergent and their workspace stale. Revert-by-id is safe only when your operation
   touched nothing of anyone else's, and its description (`rebase ... and descendants`) does not
   tell you that. Recover with a forward edit, such as recreating the bookmark.
3. **A bare `jj restore`, `jj abandon` or `jj new` in a shared checkout takes co-tenants'
   uncommitted work;** an unscoped `jj restore` reverts the whole tree. The same holds for any
   revset that is not your own change IDs: `divergent()`, `mine()`, `empty()`, `main@origin..`
   and `all:` match every session's commits. Name the change IDs you created, one by one.
4. **Your own change ID is necessary, not sufficient.** Another workspace's `@` may sit on top of
   it, and abandoning your own merged or divergent commits rebases that child. After a
   squash-merge, delete the bookmark, forget your workspace and leave the merged commits alone;
   abandon only when `jj log -r "descendants(<id>) ~ <id>"` is empty. A cross-session abandon
   also makes the other workspace stale; its edits survive (`update-stale` snapshots them first,
   see [workspace mechanics](workspaces.md)).
5. **`jj restore --from @- <explicit paths>` in your own workspace is safe.** It is the
   sanctioned fail-before technique when a test must be shown failing without the fix. If you
   need more than that, copy the bytes aside first.
6. **A probe that creates commits is cleaned up by reading commits, not the working copy.**
   `jj restore` + `rm` + `jj abandon @` can leave a clean `jj status` while commits the probe's
   `jj squash`, `commit` or `new` created still carry the probe file. Prefer a throwaway repo under
   `mktemp -d`; when the probe must run in the real store, list the change IDs it created and
   abandon those by ID.
7. **No undo loops.** After a failed command, inspect state and make one deliberate, path-scoped
   fix; a second corrective command without a fresh read compounds the damage.

## Identify workspaces and owners by the field the tool owns

`jj workspace list` never renders a path you can grep for. Most rows carry no path; the rest are
relative to the repository root, and only for workspaces resolvable on the machine asking.
Converting your absolute path to a relative one still misses the rest. Match the workspace name,
anchored as `^<name>:`, and never infer an orphan from an absent path.

Before treating a commit as a sibling's, find out whose it is. When a moved chain's bookmark
carries no session trailer, `jj op log | grep <bookmark>` names the workspace that pushed it; it
can be your own forgotten workspace. For a stray workspace whose candidate owners all say "not
mine", compare its working-copy commit's timestamp with each candidate's `jj op log`
workspace-add time.

A conflicted local bookmark prints `These bookmarks have conflicts` in every `jj status` until it
is forgotten or resolved, and a warning that is always present teaches readers to skim warnings.
Clear promptly, but only the bookmarks you created. For anyone else's, first measure what it
points at: whether the commit is still visible (`jj log -r <commit>`) and whether its content is
already on main, checked by subject or content rather than `git merge-base --is-ancestor`, which
reports every squash-merged commit as not on main. Tell the owner which commit it pointed at, then
forget it. If the owner cannot be reached, give that as the reason when you forget it.

## Immutable refusals

**`Error: Commit <id> is immutable` on an unpushed commit can be the cross-session guard.**
Agent sessions' jj configuration makes other sessions' non-empty unpushed commits immutable. Do
not rewrite another lane's work; `--ignore-immutable` is legitimate only against that guard, for a
commit your own lane owns.

**The guard is not the only source of a refusal.** `immutable_heads()` is
`builtin_immutable_heads() | (… ~ present(@) ~ empty() ~ description(…))`, and each subtraction
applies only to the second operand. Nothing subtracts from `builtin_immutable_heads()`, which
includes `untracked_remote_bookmarks()`. A PR-tracking ref (for example `<number>@pr`) can land on
a head after the PR exists, so a commit you were mutating refuses later without being touched or
pushed by you: pushed and immutable are independent in both directions. Remedy: `jj new` first,
then mutate the fresh child. Never use `--ignore-immutable` against a builtin refusal; it
rewrites whatever the real ref pins. Full semantics: [workspace mechanics](workspaces.md),
"The cross-session immutability guard".

**A refusal is silent under suppressed stderr.** `jj restore` exits 1 with empty stdout and the
whole error on stderr, so a script running it under `2>/dev/null` carries on and reports on an
unmutated tree, every result a false negative. Before trusting any result, prove the mutation
applied: grep the file for a string only the mutated version has (jj 0.45.1-sami).

**In a fork, `Commit X is immutable` on a rebase is a stale pin, not a protection.** jj's default
`immutable_heads()` includes `untracked_remote_bookmarks()`, so a superseded release ref a fetch
re-materialized, or another fork's PR head, freezes every commit beneath it, including your branch
tips. Never `--ignore-immutable` a rebase or squash: `jj squash` refusing "would rewrite N
immutable commits" is the load-bearing guard in a shared store, and overriding it rewrites
whatever the pin is, such as the release merges. Never substitute `jj duplicate` either: new
commit IDs mean the release can no longer match the branch by change ID. Find the pin with
`jj log -r 'immutable_heads() & descendants(<rev>)'`. In a knives-managed fork, `knives start` sets
the repository's rule to trunk, tags and the trunk by name on every knives remote
(`trunk() | tags() | remote_bookmarks(exact:"<trunk>", exact:"upstream") |
remote_bookmarks(exact:"<trunk>", exact:"origin")`, …) where the repository configuration states
none; a rule someone already stated is left, and `knives status` reports it as
`immutable-heads-rule`. The rebase then goes through. Moving many members at once is
`knives release rebase`, never per-branch jj.

## Every rewrite moves what is stacked on it

**`jj rebase -b @` and `jj rebase -s <root of your chain>` move every chain stacked on yours.**
`-s` rebases the root with all its descendants, so another lane's commits built on your head get
new IDs; their workspaces go stale, their next snapshots make divergent copies, and uncommitted
edits can be dropped. In a shared store, run this as its own command and read it before you type
the rebase:

```bash
jj log -r '(descendants(roots(main@origin..@)) ~ ::@) & ~empty()'
```

Do not chain it into the rebase: a chained check prints and the rebase runs anyway. Keep
`~empty()`, because other workspaces' empty working copies parented on your head count as
descendants. Anything it still prints is someone else's work that the rebase will rewrite. A
check that prints nothing may be broken (an unquoted `-T` template prints nothing), so print IDs
and read them.

- **`jj rebase -r <your range>` is worse, not safer.** It re-parents the stacked chain onto your
  old base, so the other lane's commit gets a new ID and loses your commits' files from its tree
  (`Rebased 1 descendant commits`).
- **`jj duplicate <your range> -d main@origin`** puts your chain on a new base without touching
  anything stacked on it: the stacked commit keeps its ID and its full tree.
- **`jj abandon` moves descendants too:** it rebases them onto the abandoned commit's parent
  (`Rebased 1 descendant commits onto parents of abandoned commits`). Abandoning your own
  divergent copy counts if another lane rebased onto that copy. Run the same descendants check
  before an abandon.
- **Any rewrite of a commit someone is stacked on moves them,** because jj rebases descendants
  automatically: amending it, squashing into it, or only redescribing it
  (`jj describe -r <rev>` prints `Rebased 1 descendant commits`). On a branch another lane may
  have stacked on, make every further change a child commit, created before you edit with
  `jj new <sha>`. The working copy is a commit, so editing while `@` is a pushed or reviewed head
  amends that head at the next snapshot.

**To move a child stack onto a rewritten parent, compute `-s`; never read it off `jj log`.**
Starting a commit too late leaves a parent that is not an ancestor of the new parent head, so jj
merges against a stale base and every file in the chain conflicts; change-ID prefixes overlap
across a fork, so choosing by eye is the risk. Use
`jj rebase -s 'roots(<the commit your stack sits on>..<child>)' -d <new-parent>`. When the parent
was rewritten elsewhere and fetched, the old parent stays an ancestor of the child, and
`roots(::<child> ~ ::<new-parent>)` selects the old parent, which moves it onto the new one and
conflicts the whole chain. When the parent was rewritten in the same repository, jj has already
rebased the children and both forms agree. Before and after, check that
`jj diff --name-only --from 'heads(::<child> & ::<parent>)' -r <child>` lists the same files and
that `(::<child> ~ ::<parent>) & conflicts()` is empty; the `conflicts()` check tells the two
cases apart.

## Colocation, LFS and worktree registration

**A `--no-colocate` workspace has no `.git`, and this fleet's jj configuration assumes one.** Plain
`jj workspace add` is safe: `git.colocate` defaults true, so the new workspace gets a git worktree
and LFS files materialize. The trap needs two conditions together. First, `~/.dotfiles/.jjconfig.toml`
enables `git.filter` with `git.filter.drivers.lfs.required = true` (stock jj has
`git.filter.enabled = false` and `fsmonitor.backend = "none"`; verify with
`jj config list --include-defaults -T 'source ++ "|" ++ name'`, whose `source` field distinguishes
`default` from `user`, where the bare list merges them). Second, the add is `--no-colocate` or
runs where colocation is off. Then the add aborts partway (`Failed to call the lfs filter to
convert ...`; `git lfs filter-process` needs a git repository), leaving a partial working copy,
and subprocess `git` calls (`check-ignore`, `ls-files`) exit 128, where a colocated workspace
gives 0 or git's ordinary codes (`check-ignore` exits 1 on a non-ignored path). The unblock,
every jj command there with
`--config git.filter.drivers.lfs.required=false --config fsmonitor.backend=none` and absolute
paths, exits 0 but leaves LFS files as pointer text (`Warning: Failed to use filter to convert
some files`; a 127-byte pointer where the data should be), so tooling reads pointers silently.
Prefer the colocated add.

**A new workspace inherits colocation from the workspace you run `jj workspace add` in.** jj
colocates it only if the current workspace has a valid `.git` at that moment; `git.colocate` does
not override that (jj 0.45.1-sami).

**Inside an agent box, a worktree entry dies to another box's prune unless it is locked.** A
colocated add writes an unlocked `<store>/.git/worktrees/<dir basename>`, and a bare
`git worktree prune` from any other box, which cannot see your box's paths, deletes it. jj stays
healthy while git fails with `fatal: not a git repository: <store>/.git/worktrees/<name>`.
`shims/jj` locks the worktree `jj workspace add` creates, and in an agent session `shims/git`
locks every `git worktree add` and refuses a bare `git worktree prune`. A shell that ran `jj` or
`git` before the shims were installed keeps the unshimmed path hashed until `hash -r`. Where the
lock is missing, the named exception is
`git -C <store> worktree lock <path> --reason '<why>'`.

- Remove your own workspace with `jj workspace forget` (it prints `Removed Git worktree for
  "..."`), never `git worktree prune`. Stock jj 0.45.1's forget runs a repository-wide prune,
  fixed from `0.45.1-sami.20260909-184010`, so check `jj --version` before forgetting on a
  shared store.
- To diagnose, read the workspace's own `.git`. No file means not colocated: git does not work
  there, so run git and `gh` from a colocated checkout. A `gitdir:` line naming a missing
  directory means the entry was deleted: rebuild it by `disk-hygiene`'s recipe, then lock it.
- Never match `jj workspace list` against `ls <store>/.git/worktrees/`: git names each entry after
  the directory basename plus a collision number, so the names differ.
