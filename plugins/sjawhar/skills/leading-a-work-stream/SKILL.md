---
name: leading-a-work-stream
description: Use when you lead a work stream or team of agents, when an agent asks you to approve, confirm or review its step or pull request, when you want to know the state of the stream, when an agent starts a long project, or when you consider building tooling or a restriction for agents.
---

# Leading a work stream

A lead raises the stream's numbers by making its agents faster. A lead is not a gate. Every reply an agent waits for is time the stream loses.

The stream's own facts (its numbers, scoreboard format, plan, dashboard) are in its issue spec or the organization's stream document. Read them there.

## What a lead does

1. **Sets direction**: the numbers and targets, the bottleneck, and which experiments run in what order, written where every agent can read them. Open the stream's issue with three lines above its targets: the problem the stream exists to solve, the one approach it commits to, and what it will not do. Ask the user the three questions once, in one message, with a draft answer for each taken from the user's own words. Push once on an answer that decides nothing. Agents use the third line as a constraint on their own choices.
2. **Removes obstacles** its agents cannot: access, a decision above their authority, a conflict with another stream.
3. **Checks results against the numbers**, not against its own reading of the work.
4. **Coaches each agent's loop**: is it measuring, predicting, trying enough designs? Procedure, not solutions. An experiment on a number with a benchmark that runs in minutes runs through `/autoresearch`, with the expected gain in its notes before each run and the change run again before it is kept; ce-optimize instead when the result is noisy or has several objectives. A one-off change still gets its prediction written in the scoreboard before it starts.
5. **Makes the stream faster**: finds where agents lose time and changes the skill, tool or step that causes it.

## What a lead does not do

- Gate decisions an agent can make. A concern about a reversible step goes to the agent as a constraint, and the agent decides.
- Review every pull request. Peer review and tests are the approval.
- Require a plan, spec or confirmation to be sent to it before an agent acts.
- Ask agents for a report of each decision or each step they took. It reads results in GitHub, Dispatch and the numbers, and measures a number with a command it runs.
- Ration a shared resource (disk, VMs, compute) by approving each use. It sets the limit, and the agents apply it themselves or through a tool.
- Relay messages between agents who can talk directly.
- Give an agent orders specific to its own work (which task to launch, at what size, what to regrade). The agent has the facts of its own work; the lead does not. Give the rule and the goal (the target, the budget, the bottleneck) and let each agent choose its steps. Before you pass on a precondition from another agent, check it against the code or have its owner send it directly.

## Rules reach agents through what they read

A rule sent only by message reaches the agents online that day, in the lead's paraphrase, and is gone for the next session. Put each rule where agents read it before they act: the skill that covers the step, the stream's brief, the repository's own instructions. Write it as an instruction at the step it governs.

Keep one record of the words of the person the stream works for, each quoted with its date and source. A skill, brief, spec or message that says that person wants something links the quote. Without a quote, it names whose rule it is. When a later quote changes an earlier one, the record says which one stands, and every text that used the earlier one is changed the same day. Never shorten a quote into a stronger rule ("don't regrade his finished versions without asking him" is not "never regrade"), and never turn a direction into a number the person did not give.

## Decision rights

| The step | Who decides |
| --- | --- |
| Easy to undo: a code change, a flag, a branch, opening or merging a pull request that passed peer review and tests, a planned experiment, spend the workflow calls for (a paid-API review, compute, a VM, a model run the task needs) | The agent. It acts and reports to nobody: the lead reads the result in GitHub, Dispatch and the numbers. |
| Hard to undo: deleting data someone else created, a new financial commitment (a contract, a subscription), a message outside the organization, security, another stream's work or shared state | The agent takes it to its owner directly: Sami through the agent's own Dispatch ask, the other stream's agent or lead, the owner of the shared infrastructure. It does not pass through the lead. |

When an agent asks permission for an easy-to-undo step, reply "your call" and state any constraint it must keep. Add no check of your own before it may act. Give intent and constraints, not step-by-step approval. Do not send a "yes" to an agent that said it would not wait.

## Long projects use their workflow skill

When an agent starts a long project, check that it uses the workflow skill for that kind of work, and name the skill. For example: the `/sdd` workflow for an engineering project, `building-and-delivering-tasks` for RL task work. The skill's own review steps replace review by the lead.

## Tooling makes agents more productive

- When agents keep losing time to the same chore, start an agent (on the dev box or elsewhere) to build the tool that removes it. Do not wait to be asked.
- Do not build, file or ask for a restriction, guard or gate against a failure nobody has seen. Fix a real failure at its cause, after it happens.

## Plan the day with the team

Every morning, by 09:00 Pacific, the lead and its agents agree on Envoy what the stream will merge today, as a working team does at its daily planning: each agent says what it can land and what stands in the way, the team settles the list, and the lead writes it into the stream's record. The lead owns the list and that it is real. Each item starts with the KPI it moves and the change expected, for example "Grade wall time 7,104 s -> 5,700 s: ...". Work that moves no KPI goes on the list only when it unblocks an item that does, and names that item. During the day, an agent that will miss an item says so as soon as it knows, so the team can move work. At the end of the day, compare the KPI readings with what the list predicted, and talk about the misses.

## Knowing the state of the stream

Answer five questions about the stream from records you read yourself (GitHub, Dispatch, the numbers):

1. What is the target?
2. What is the actual value now?
3. What is the obstacle, and which one is being worked on?
4. What is the next step, and what should it do to the number?
5. When will we know?

Message an agent to plan the day with it, to remove an obstacle or to change its direction, not to collect status.

## Count yourself as a wait

Agents waiting on your reply are a queue. Keep it empty: answer at once, or reply "your call". When one kind of question keeps arriving, write the answer into the stream's plan or skill, or build the tool that makes the question unnecessary.

## Delegate

Use subagents and peer agents for review, research and skill improvement. Delegation keeps the responsibility with you, so follow each delegated result through to the number.

## Improving this skill

When a rule here failed or was missing, edit this file directly. Put the evidence (the transcript, message or number) in the commit message.

## Sources

- Bezos, [2015 shareholder letter](https://www.sec.gov/Archives/edgar/data/1018724/000119312516530910/d168744dex991.htm): reversible decisions go to individuals, fast; a heavy process on them causes "slowness, unthoughtful risk aversion".
- DORA, [Streamlining change approval](https://dora.dev/capabilities/streamlining-change-approval/): external approval slows delivery and does not cut failed changes; peer review plus automation works; more process after problems makes things worse.
- US Army, [ADP 6-0 Mission Command](https://armypubs.army.mil/epubs/DR_pubs/DR_a/ARN47032-ADP_6-0-000-WEB-1.pdf), 1-37 to 1-44: act within intent; delegation cuts response time; responsibility stays with the commander.
- Google re:Work, [research behind great managers](https://rework.withgoogle.com/intl/en/guides/following-the-data-the-research-behind-great-managers): good managers coach and "empower the team without micromanaging".
- Rother & Liker, [A Kata for PDCA](https://cdn.prod.website-files.com/68b9f0686639ca6b3c064d61/68dd8a5720bf6d3042bafc50_a_kata_for_pdca_-_target_summer_2014.pdf), and [LEI, Kata](https://www.lean.org/lexicon-terms/kata/): the five questions; the coach gives procedure, not solutions.
- Reinertsen, [Product Development Flow, ch. 1](http://lpd2.com/wp-content/uploads/2013/06/ReinertsenFLOWChap1.pdf): queues are the largest hidden cost.
- [Summary of Grove's High Output Management](https://www.becausetech.rocks/blog/high-output-management/): meddling makes people refer problems up; follow delegated work through.
