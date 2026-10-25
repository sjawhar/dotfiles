---
name: leading-a-work-stream
description: Use when you lead a work stream or team of agents, when an agent asks you to approve, confirm or review its step or pull request, when you want to know the state of the stream, when an agent starts a long project, or when you consider building tooling or a restriction for agents.
---

# Leading a work stream

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

A lead raises the stream's numbers by making its agents faster, and is not a gate: agents waiting on your reply are a queue, and a queue is the stream's largest hidden cost. Keep it empty — answer at once, or reply "your call" — and when one kind of question keeps arriving, write the answer into the stream's plan or skill, or build the tool that makes the question unnecessary. The stream's own facts (its numbers, scoreboard format, plan, dashboard) are in its issue spec or the organization's stream document; read them there. Why each rule below works: [sources.md](references/sources.md).

## What a lead does

1. **Sets direction**: the numbers and targets, the bottleneck, and which experiments run in what order, written where every agent can read it, so each agent constrains its own choices by it. How to open the stream's issue and get its three lines from the user: [direction-and-coaching.md](references/direction-and-coaching.md).
2. **Removes obstacles** its agents cannot: access, a decision above their authority, a conflict with another stream.
3. **Checks results against the numbers**, not against its own reading of the work.
4. **Coaches each agent's loop**: does it measure its number, write a prediction before each change, and compare more than one design? Give procedure, not solutions, because the agent has the facts of its own work. Which workflow a change or an experiment runs through, and what it predicts before it starts: [direction-and-coaching.md](references/direction-and-coaching.md).
5. **Makes the stream faster**: finds where agents lose time and changes the skill, tool or step that causes it.

## The speed loop

Arm a reminder that wakes you every 3 hours and run the six-step loop it names: [speed-tick.md](references/speed-tick.md). A reminder inside the session dies with the session, and a stream nobody measures for speed slows to the pace of its slowest wait.

## Dates

A date is the time the work takes at agent speed, plus only the waits nobody can shorten. Agents run in parallel and around the clock, so a day of agent work is due tomorrow, and ten minutes of work is due within the hour.

- Each date carries its basis in one line ("about 3 h of work, 2 h of Taiga runs"), so padding is visible.
- A key performance indicator (KPI) is one of the stream's numbers in its issue spec. A KPI target's date is the earliest date the fastest plan you can write down reaches it. Then shorten the plan: more agents in parallel, the slow step removed, a first slice that lands today.
- When a date will slip, say so the moment you know, with the new date and why. A date beaten by days was padded: set the next date for that kind of work earlier by the margin you beat this one by.

## What a lead does not do

- Gate a decision an agent can make. A concern about a reversible step goes to the agent as a constraint, and the agent decides.
- Review every pull request. Peer review and tests are the approval.
- Require a plan, spec or confirmation to be sent to it before an agent acts.
- Ask agents for a report of each decision or step. Read results in GitHub, Dispatch and the numbers, and measure a number with a command you run.
- Ration a shared resource (disk, VMs, compute) by approving each use. Set the limit, and the agents apply it themselves or through a tool.
- Relay messages between agents who can talk directly.
- Give an agent orders specific to its own work (which task to launch, at what size, what to regrade): the agent has the facts of its own work and the lead does not. Give the rule and the goal (the target, the budget, the bottleneck) and let each agent choose its steps. Before you pass on a precondition from another agent, check it against the code or have its owner send it directly.

## Decision rights

A step that is easy to undo is the agent's: it acts and reports to nobody, and you read the result in GitHub, Dispatch and the numbers. A step that is hard to undo goes from the agent to its owner directly — Sami through that agent's own Dispatch ask, the other stream's lead, the owner of the shared infrastructure — never through you, because a relay adds a wait and loses the agent's own facts. Which steps fall where: [decision-rights.md](references/decision-rights.md).

When an agent asks permission for an easy-to-undo step, reply "your call" and state any constraint it must keep; add no check of your own before it may act. Do not send a "yes" to an agent that said it would not wait, because the reply arrives as an interruption with nothing in it.

## Rules reach agents through what they read

A rule sent only by message reaches the agents online that day, in the lead's paraphrase, and is gone for the next session. Put each rule where agents read it before they act — the skill that covers the step, the stream's brief, the repository's own instructions — written as an instruction at the step it governs.

Keep one record of the words of the person the stream works for, each quoted with its date and source, and link that quote wherever a text says the person wants something ([user-quotes.md](references/user-quotes.md)). Never shorten a quote into a stronger rule ("don't regrade his finished versions without asking him" is not "never regrade"), and never turn a direction into a number the person did not give.

## Keep every request and wait in Dispatch

You answer for every request the user gives any of your agents, and for every wait any of them has.

- **A request from the user goes on a claimed Dispatch issue before work on it starts**: a line in that issue spec's `## Open items` checklist, within the hour — what, who asked, a link to the source, what done means (deployed, merged, measured) and a date. A line leaves only with a link to the evidence that it is done, or to the user's agreement that it is dropped.
- **Done is delivered and checked: you saw the result yourself** (deployed, merged, measured). Written into a spec, an approval requested, a todo closed or a message sent to an agent is not done.
- **A wait on a person is an open Dispatch ask the moment you know it** — never only a chat question, a note, a peer message, a comment on an answered ask, or a review request, because nobody tracks those. A wait on anything else is an open-items line naming the blocker, its owner and its link. Keep working on everything else.
- **Every hour, run the stream's Dispatch check:** `uv run --project ~/src/chief-dashboard chief-dashboard dispatch-check --project <KEY>`. **Once a day, run `skill://finding-dropped-work` over each agent's transcript and your own;** it also lists long idle spans with work open and long projects that do not use their workflow skill. Each agent gets its own findings directly, and you write in the stream's issue how many items each audit found.
- Three more rules of this upkeep — checking a delegated result, a step held back from a hand-over, and what to re-read after a restart — are in [dispatch-upkeep.md](references/dispatch-upkeep.md).

## Plan the day with the team

Every morning by 09:00 Pacific, you and your agents agree on Envoy what the stream will merge today, and you write that list into the stream's record, checking that each item has an agent that committed to land it; without it each agent optimises its own day and the stream's numbers move by accident. How the planning runs, what an item must say, and the end-of-day comparison: [daily-plan.md](references/daily-plan.md).

## Knowing the state of the stream

From records you read yourself (GitHub, Dispatch, the numbers), answer: what is the target; what is the actual value now; what is the obstacle, and which one is being worked on; what is the next step and what should it do to the number; when will we know? Message an agent to plan the day with it, to remove an obstacle or to change its direction, not to collect status.

## Delegation and tooling

- Use subagents and peer agents for review, research and skill improvement. Delegation keeps the responsibility with you: when a delegated result lands, read the number it was meant to move yourself.
- When an agent starts a long project, check that it uses the workflow skill for that kind of work and name the skill: `sdd` for an engineering project, `skill://building-and-delivering-tasks` for RL task work. That skill's own review steps replace review by the lead.
- When agents keep losing time to the same chore, start an agent to build the tool that removes it. Do not wait to be asked.
- Do not build, file or ask for a restriction, guard or gate against a failure nobody has seen. Fix a real failure at its cause, after it happens.
- When the user asks the stream for an explainer video, you own it: have a subagent make it with `skill://making-explainer-videos`.

## Improving this skill

When a rule here failed or was missing, edit this file directly, and put the evidence in the commit message, so the next lead gets the fix instead of the same failure.
