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

Architectural changes (under `brainstorming`) are a conversation with Sami in the issue's spec, on
Dispatch per `dispatch-brainstorming`. Start from the problem and its evidence, with sections that
follow the topic.

- Put each open question right after its section as a decision block (on Dispatch, an ask block): the options, what each costs, your recommendation.
- Record each answer there with its date, a link to his answer, and its reason in your own words. It settles what it answers.
- Ask for approval only when each block carries his answer, folded into the text, and only if the spec still proposes something he did not settle. Name those proposals in the request, never an open question.
- Without Dispatch, hold the same conversation on the tracker you have, or in chat when none is reachable.
- Outside that conversation, chat is for execution and for bounded designs already under live discussion.

## Workspaces

You keep your starting workspace. Each worker uses the one its task names, normally the same tree
for sequential steps. Make a second one only when two workers edit different files at the same
time: a full clone (`git clone <url> ~/.worktrees/<repo>/<name>`, not `git worktree add`) outside an
agent box (`scripts/agentbox`), `~/boxes/<box>/<name>` inside one. Never `/tmp`: it is cleared between
sessions and an agent box does not share it, so a resumed worker finds no tree.
