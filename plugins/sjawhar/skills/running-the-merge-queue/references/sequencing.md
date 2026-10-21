# Authority, sequencing and shared stacks

Read the current queue ruling record first. Nothing here creates holds, merge windows, credentials or permission.

## Priority and holds

- Equally ready same-file siblings with no stronger priority or dependency: the oldest approved PR goes first. Serialize only the shared boundary. The owner already repairing a collision normally absorbs its resolution.
- A hold that needs a conflict check needs the actual three-way composition, not overlap guessed from filenames.
- A lifted hold ends at once. On the next wake, re-read the live head, verdicts and mergeability. An old "held" label is not an observation.
- A repeated "escalate next time" means: read the source and use the right route now.
- For a defined coordinated migration batch, record the intended published parent order once, when the batch is packet-ready, before re-parenting and re-gating. A later sibling does not reopen it. Ordinary branch work gets no serial queue from this.

## Answers and owner identity

- Read a Dispatch answer from its ask and replies, not a GitHub substitute: `dispatch read --ref 'dispatch://<KEY>/ask/<id>'`. If the answer was on GitHub, read that thread.
- Find the originating owner from the linked PR and current role record. If it has not acted, relay the pending ruling once, with source and exact scope. Owners ask their own questions.
- Find a recipient from the PR's `Omp-Session:` commit trailer and the ownership record, never a remembered prefix: `gh api --paginate repos/O/R/pulls/N/commits --jq '.[].commit.message'`.

## Ordinary stacks and squash parents

- Use `gh-stack` for stack operations and `using-jj` for version control. Identify each child's intended published parent and actual fork point before changing it.
- A squash merge makes a new commit without the old branch commits. Retargeting a child's base does not remove them from its history.
- Enumerate children with a limit that covers the population, or a paginated API. A capped list is not exhaustive: `gh pr list --repo O/R --base <merged-branch> --state open --limit <complete-population>`.
- A safe restack replays only the identified unmerged owned commits, never the squash-merged parent's contribution. Keep the old base and tip before the remote parent branch disappears. Afterward, check new parents, remote file scope, review confirmation and acceptance.
- In a non-native stack, integrating the topmost PR into its parent first can avoid repeated restacks. Every constituent gate and the intended final trunk still apply. Never change repository merge settings to solve stack order.

## Shared-store safeguards for every rewrite

- Before you recommend a mutation, the owner inspects descendants, bookmarks and workspace ownership with `using-jj`. Explicit IDs are necessary but not enough: `jj rebase -r <id>` can move descendants too. Coordinate other owners' revisions first.
- Never mutate broad sets: `mine()`, `all:`, a trunk range, or a whole branch whose descendants others own. A broad `jj rebase -b <bookmark>` is not a safe shared-store recipe.
- Tip-only selection can drop a required unmerged parent. Replaying a squash-merged parent is the opposite mistake. Preserve a required unmerged parent, or select the owned unmerged range explicitly. Choose from the actual graph, not from a favorite rebase flag.
- Never repair shared-store mistakes with `jj undo`, `jj op restore`, `jj op revert` or an immutable-guard bypass: each can affect other sessions. The owner makes an inspected forward correction. You never clean up their revisions.

## Native GitHub Stacks

- The PR's `.stack` field tells a native stack from dependent branches. The per-PR merge endpoint can refuse native members. That is a stack mechanics error, not permission for an admin token.
- Use the supported `gh stack` path under approved identity routing. Its squash merges the selected prefix into trunk, bottom to top. Read its current `--help` and the full selected member set first.
- Every selected member needs its own head-pinned evidence and protection. Never use a path that loses them.
- Before merging, get an actual three-way replay against the intended trunk in stack order. A member's green mergeability can describe only its intermediate base. A refusal that names no file does not prove the stack is conflict-free.

## Migration consumers

- Use the repository's migration graph and deployment contract, verified against current trunk at merge time. With a single linear Alembic head, `down_revision` must match the current intended head. The newest filename does not establish the graph.
- For a tightening constraint, the owner's evidence covers writers added on trunk since the branch: application inserts, ORM paths, fixtures. Two green PRs can compose into an invalid writer/constraint pair.
- These are integration evidence checks, not permission to review or rewrite the migration.
