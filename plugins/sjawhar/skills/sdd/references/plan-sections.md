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
time: a full clone (`git clone <url> ~/.worktrees/<repo>/<name>`, not `git worktree add`) outside an
agent box (`scripts/agentbox`), `~/boxes/<box>/<name>` inside one. Never `/tmp`: it is cleared between
sessions and an agent box does not share it, so a resumed worker finds no tree.

**Clone with `--reference <canonical checkout>` when the canonical checkout is on the same disk.**
A plain clone materialises its own object store. On 2026-10-30 five exports of one repository put
31 GB under `~/.worktrees`, 26 GB of it the same objects three and four times over, and the root
disk reached zero with 221 GiB free on another volume. `--reference` borrows those objects instead.

**The hazard, and the condition that removes it:** a borrowing clone breaks if the canonical
checkout prunes an object it is using, which `git gc` can do without knowing the borrower exists.
So borrow only from a checkout whose automatic maintenance is off (`git config gc.auto 0`,
`git config maintenance.auto false`), and never from a throwaway tree. Where neither holds, take
the plain clone and the disk cost.
