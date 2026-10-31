# The plan's required sections, and major design changes

## Sections every plan carries

| Section | Contents |
|---|---|
| `## Hardening ledger` | Empty at first. |
| `## End-to-end verification plan` | Per slice: workflow class and user-visible outcome. The real production transport and environment. The production identity and grant path (a human grant for human-facing work). The existing driver, or a task to build one. The cheapest real shared-resource substitute (staging, a branch run), never a local simulation of the transport. |
| `## Skill catalog` | Each step's required worker skills. |
| `## Contract change census` | For boundary tightenings as `opening-a-pr` defines them: the search commands, each hit's disposition, and the rollout line. Trace producers, consumers and shared builders in this repo and its siblings, including callers that rebuild values without spelling the field or endpoint (concatenation, `**kwargs`, a config map), which a name search does not find. The brief's caller list is not evidence. |
| `## Permission source census` | Each new or widened permission, role, scope or capability flag, with its real holder in every shipped environment. The group, profile, binding or seed that grants it. The enforcement point. A test that fails for a permission with no holder. Manual grants and test-only identities are not production sources. |

## A major design change

An architectural change is a conversation with Sami in the issue's spec. On Dispatch, write and
run that spec as `dispatch-writing-specs` says: its outline, a proposed answer to each point, and an
ask only for what needs his authority, taste or risk appetite. That skill holds the spec rules he
approved, so repeating them here would let the two drift apart.

- Without Dispatch, hold the same conversation on the tracker you have, or in chat when none is reachable.
- Outside that conversation, chat is for execution and for bounded designs already under live discussion.

## Workspaces

You keep your starting workspace. Each worker uses the one its task names, normally the same tree
for sequential steps. Make a second one only when two workers edit different files at the same
time.

**One clone per repository, worktrees from it.** Outside an agent box (`scripts/agentbox`), make
every further tree with `agent-workspace`, which cuts it from the canonical checkout
`~/src/<repo>` so the repository's objects exist once on the machine:

```sh
agent-workspace new <repo> <name>          # ~/.worktrees/<repo>/<name>, prints the path
agent-workspace list <repo>                # what each existing tree is, and which are broken
agent-workspace rm <repo> <name>           # when the work ends
```

Inside an agent box, use a sibling of your workspace in the box's own tree (`<boxes>/<box>/<name>`:
`/mnt/nvme/<user>/agentbox/boxes` on a machine with that disk, else `~/boxes`), which the box
already clones from its template with the objects hardlinked. Never `/tmp`: it is cleared between
sessions and an agent box does not share it, so a resumed worker finds no tree.

**Never `git clone` a repository a checkout of which is already on the machine.** A plain clone
materialises its own object store. On 2026-10-30 nine children of
`~/.worktrees/binary-equivalence-taiga` each held one — 19.0 GiB for a repository whose canonical
checkout is 2.0 GiB — and the root disk reached 14 MiB free with 221 GiB free on another volume.
Through `agent-workspace`, a second tree of that repository costs 202 MiB of files and 2.1 MiB in
the source. Where a tree genuinely needs refs of its own, `agent-workspace new --separate-repo`
makes a `git clone --shared`, which borrows the source's objects; `--independent` is the flag
that pays for a private copy, and it says what that cost.

**The hazard, and what handles it:** a worktree and a `--shared` clone both stop working if the
canonical checkout is removed or moved, and a `--shared` clone can also lose an object the
source's `git gc` pruned without knowing a borrower existed. `agent-workspace new --separate-repo`
turns that maintenance off in the source and records the borrower; `agent-workspace list` names
every tree whose source has gone and says what to do about it. Removing a worktree is
`agent-workspace rm`, not `rm -rf`: the source keeps a registration until it is cleared (699 of
them had accumulated in that checkout), and a jj workspace stays in its store's op log until
`jj workspace forget`.
