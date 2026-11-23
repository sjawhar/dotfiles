# Plan sections, workspaces, and a major design change

## Sections every plan carries

- `## Hardening ledger`: empty at first.
  - Add a row for each shortcut you or a worker takes, when it happens.
  - Add a row for each part of the request whose covering change or check (a diff hunk, a test or a scenario run) you or a worker cannot name, when it happens.
  - Step 1 of `opening-a-pr` finishes each row or names its blocker before the PR opens.
- `## End-to-end verification plan`, per slice:
  - its class and user-visible outcome;
  - the real production transport and environment;
  - the production identity and grant path (a human grant for human-facing work);
  - the existing driver, or a task to build one;
  - the cheapest real shared-resource substitute (staging, a branch run), never a local simulation of the transport.
- `## Skill catalog`: each step's required worker skills.
- `## Contract change census`: for a change that tightens a contract, as `opening-a-pr` defines it: the search commands, each hit's disposition, and the rollout line.
  - For the census, trace producers, consumers and shared builders in this repo and its siblings yourself.
  - Include in that trace the callers that rebuild a value without spelling the field or endpoint (concatenation, `**kwargs`, a config map), which a name search does not find.
  - The brief's caller list is not evidence.
- `## Permission source census`: each new or widened permission, role, scope or capability flag, with:
  - its real holder in every shipped environment;
  - the group, profile, binding or seed that grants it;
  - the enforcement point;
  - a test that fails for a permission with no holder.
- In the permission source census, manual grants and test-only identities are not production sources.

## A major design change

- Hold an architectural change as a conversation with Sami in the issue's spec, written as `dispatch-writing-specs` says:
  - its outline;
  - a proposed answer to each point;
  - an ask only for what needs his authority, taste or risk appetite, or the reason for an instruction he gave without one.
- Without Dispatch, hold the architectural change on the tracker you have.
- Without Dispatch, hold the architectural change in chat when no tracker is reachable.
- Outside that conversation, use chat only:
  - to carry out the plan;
  - to settle a design question Sami is discussing with you in the current chat whose answer changes no shared contract, slice or decision he approved.
- Any other design question goes into the spec.

## Workspaces

- Keep your starting workspace.
- Each worker uses the workspace its task names, normally the same tree for sequential steps.
- Make a second tree only when two workers edit different files at once.
- **Outside an agent box:**
  - make each further tree with `agent-workspace new <repo> <name>` (`agent-workspace --help`);
  - remove each further tree with `agent-workspace rm`, not `rm -rf`.
- Do not `git clone` a repository already checked out on the machine: a plain clone copies its whole object store.
- When a tree needs refs the source must not see, use `agent-workspace new --separate-repo`, a `git clone --shared` that borrows the source's objects.
  - `--independent` makes a full clone that pays for a private copy of every object.
- Do not remove or move the canonical checkout (`~/src/<repo>`): every worktree and `--shared` clone of it stops working.
  - `agent-workspace list` names each broken tree and says what to do.
- Inside an agent box, put each further tree beside your workspace in the box's own tree (`<boxes>/<box>/<name>`).
- Never `/tmp`: it is cleared between sessions and a box does not share it, so a resumed worker finds no tree.
