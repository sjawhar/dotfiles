# Plan sections, workspaces, and a major design change

## Sections every plan carries

- `## Hardening ledger`: empty at first.
- `## End-to-end verification plan`, per slice: its class and user-visible outcome; the real production transport and environment; the production identity and grant path (a human grant for human-facing work); the existing driver, or a task to build one; the cheapest real shared-resource substitute (staging, a branch run), never a local simulation of the transport.
- `## Skill catalog`: each step's required worker skills.
- `## Contract change census`: for a change that tightens a contract, as `opening-a-pr` describes. Trace producers, consumers and shared builders in this repo and its siblings yourself; the brief's caller list is not evidence.
- `## Permission source census`: each new or widened permission, role, scope or capability flag, with its real holder in every shipped environment; the group, profile, binding or seed that grants it; the enforcement point; and a test that fails for a permission with no holder. Manual grants and test-only identities are not production sources.

## A major design change

Hold an architectural change as a conversation with Sami in the issue's spec, written as `dispatch-writing-specs` says: its outline, a proposed answer to each point, and an ask only for what needs his authority, taste or risk appetite. Without Dispatch, hold it on the tracker you have, or in chat when none is reachable. Outside that conversation, chat is for execution and for bounded designs already under live discussion.

## Workspaces

- Keep your starting workspace. Each worker uses the one its task names, normally the same tree for sequential steps; make a second only when two workers edit different files at once.
- Outside an agent box, make each further tree with `agent-workspace new <repo> <name>` and remove it with `agent-workspace rm`, not `rm -rf` (`agent-workspace --help`). Do not `git clone` a repository already checked out on the machine: a plain clone copies its whole object store.
- Inside an agent box, put it beside your workspace in the box's own tree (`<boxes>/<box>/<name>`).
- Never `/tmp`: it is cleared between sessions and a box does not share it, so a resumed worker finds no tree.
