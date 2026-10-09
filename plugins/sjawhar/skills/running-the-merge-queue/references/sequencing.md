# Authority, sequencing and shared stacks

Read the current queue ruling record before applying any sequence. Nothing here
creates holds, merge windows, credentials or permission.
The controller requests the owner's action; it does not rebase or push for them.

## Priority and holds

Use Sami's current recorded priority. Otherwise choose by value and dependencies,
including the cost a large corpus merge imposes on its peers. For equally ready
same-file siblings without a stronger priority/dependency, the oldest approved PR
goes first. Serialize only the shared boundary, not every unrelated PR. The owner
already repairing a collision normally absorbs its resolution.

If a hold requires a conflict check, require the actual three-way composition,
not guessed overlap from filenames. A lifted hold stops immediately. Re-read the
live head, verdicts and mergeability on the next wake; an old “held” label is not a
current observation. Repeated “escalate next time” is a reason to read the source
and use the appropriate route now, not another copied status.

For a defined coordinated migration batch, record the intended published parent
order once when the batch is packet-ready, before individual re-parenting and
re-gating. A later sibling does not itself reopen that batch decision. This is not
a serial-queue rule for ordinary branch work.

## Current answers and owner identity

Read a native Dispatch answer from its ask and replies, not a GitHub substitute:

```bash
dispatch read --ref 'dispatch://<KEY>/ask/<id>'
```

Use the linked PR and current role record to find the originating owner. Check
whether it already acted, then relay a still-pending ruling once with its source
and exact scope. If the answer was actually on GitHub, read that specific thread.
Owners ask their own questions; this does not make the queue their question relay.

Locate a recipient from the current PR's `Omp-Session:` commit trailer and current
ownership record, not a remembered prefix:

```bash
gh api --paginate repos/O/R/pulls/N/commits --jq '.[].commit.message'
```

If the active gate packet came from a coordinator, separately confirm with the
work owner that no review/fix round is in flight. The coordinator's gate authority
and the author's work-completion knowledge are both required.

## Ordinary stacks and squash parents

Use `gh-stack` for stack operations and `using-jj` for version control. Identify the
intended published parent and actual fork point for each child before changing it.
A squash merge creates a new commit without the old branch commits in its ancestry.
Retargeting a child's base on GitHub does not remove those commits from its history.

After merging a parent, enumerate children immediately:

```bash
gh pr list --repo O/R --base <merged-branch> --state open --limit <complete-population>
```

Use a limit covering the population or a paginated API; do not call a capped list
exhaustive. Tell each child's owner the parent result, actual base and restack
requirement in one message. Do not wait for each child to rediscover the collision.

A safe restack replays only the explicitly identified **unmerged owned commits**,
not the squash-merged parent's contribution. Preserve the old base and tip for
comparison before a remote parent branch disappears. After the owner restacks,
verify new parents, remote file scope, review confirmation and acceptance.

For an ordinary non-native stack, integrating the topmost PR into its parent first
can avoid repeatedly restacking children above an intermediate squash. It still
requires every constituent gate and the intended final trunk. Do not change repo
merge settings to solve stack sequencing.

## Shared-store safeguards apply to every rewrite

Before recommending any mutation, have the owner inspect descendants, bookmarks
and workspace ownership using `using-jj`. Explicit IDs are necessary, not enough:
`jj rebase -r <id>` can rebase descendants too. Coordinate any other owner's
revisions first. Do not mutate broad sets such as `mine()`, `all:`, a trunk range
or a whole branch whose descendants are not exclusively owned.

Selecting only a chain's tip can drop its required unmerged parent; replaying a
squash-merged parent is the opposite mistake. Preserve a required unmerged parent,
or select the owned unmerged range explicitly. A broad `jj rebase -b <bookmark>`
is not a safe shared-store recipe. Choose from the actual graph, not from a
favorite rebase flag.

Never repair shared-store mistakes with `jj undo`, `jj op restore`, `jj op revert`
or an immutable-guard bypass. All can affect other sessions. The controller does
not clean up their revisions; the owner makes an inspected forward correction.
Do not solicit a rebase merely because main advanced without a conflict.

## Native GitHub Stacks

Read the PR's `.stack` field to distinguish a native GitHub Stack from an ordinary
set of dependent branches. The ordinary per-PR merge endpoint can refuse native
members because the stack requires its asynchronous merge path. That is a stack
mechanics error, not permission to use an admin token.

Use the currently supported `gh stack` path under approved identity routing. Its
squash operation merges the selected stack prefix into trunk, bottom-to-top, rather
than just integrating the last layer into its parent. Verify current command help
and the full selected member set before executing. Every selected member needs its
own head-pinned evidence and protection; do not use a path that cannot preserve
those requirements.

Before the merge, obtain an actual three-way replay against the intended trunk in
stack order. A member's green mergeability may only describe its intermediate base,
not trunk. A refusal with no named file is not proof the stack was conflict-free.

## Migration consumers

Use the repository's own migration graph and deployment contract. Verify the graph
against current trunk at merge time, not only the packet's old base. In a repository
requiring a single linear Alembic head, that means `down_revision` must match its
current intended head; reading the newest filename alone does not establish a graph.

For a tightening constraint, require the owner's evidence to account for writers
added on trunk since the PR branched: application inserts, ORM paths and fixtures.
Two individually green PRs can compose into an invalid writer/constraint pair.
These are integration evidence checks, not permission for the controller to review
or rewrite the migration itself.
