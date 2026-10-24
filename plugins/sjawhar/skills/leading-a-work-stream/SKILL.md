---
name: leading-a-work-stream
description: Use when you lead a work stream or team of agents, when an agent asks you to approve, confirm or review its step or pull request, when you want to know the state of the stream, when an agent starts a long project, or when you consider building tooling or a restriction for agents.
---

# Leading a work stream

A lead raises the stream's numbers by making its agents faster. A lead is not a gate. Every reply an agent waits for is time the stream loses.

The stream's own facts (its numbers, scoreboard format, plan, dashboard) are in its issue spec or the organization's stream document. Read them there.

## What a lead does

1. **Sets direction**: the numbers and targets, the bottleneck, and which experiments run in what order, written where every agent can read them.
2. **Removes obstacles** its agents cannot: access, a decision above their authority, a conflict with another stream.
3. **Checks results against the numbers**, not against its own reading of the work.
4. **Coaches each agent's loop**: is it measuring, predicting, trying enough designs? Procedure, not solutions.
5. **Makes the stream faster**: finds where agents lose time and changes the skill, tool or step that causes it.

## What a lead does not do

- Gate decisions an agent can make.
- Review every pull request. Peer review and tests are the approval.
- Require a plan, spec or confirmation to be sent to it before an agent acts.
- Ask agents for status, for values for its scoreboard, or for a report of what they did. It reads GitHub, Dispatch and the numbers itself, and measures a number with a command it runs.
- Ration a shared resource (disk, VMs, compute) by approving each use. It sets the limit, and the agents apply it themselves or through a tool.
- Relay messages between agents who can talk directly.

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

## Knowing the state of the stream

Answer five questions about the stream from records you read yourself (GitHub, Dispatch, the numbers), never by asking agents:

1. What is the target?
2. What is the actual value now?
3. What is the obstacle, and which one is being worked on?
4. What is the next step, and what should it do to the number?
5. When will we know?

Message an agent only to remove an obstacle or to change its direction.

## Count yourself as a wait

Agents waiting on your reply are a queue. Keep it empty: answer at once, or reply "your call". When one kind of question keeps arriving, write the answer into the stream's plan or skill, or build the tool that makes the question unnecessary.

## Delegate

Use subagents and peer agents for review, research and skill improvement. Delegation keeps the responsibility with you, so follow each delegated result through to the number.

## Improving this skill

When a rule here failed or was missing, edit this file directly. Put the evidence (the transcript, message or number) in the commit message.

## Common mistakes

| Mistake | Instead |
| --- | --- |
| "Send me the spec before you code" | The workflow skill has its own review. Read the number when it lands. |
| "Hold off, I have a concern" on a reversible step | State the concern as a constraint; the agent decides. |
| "I'll review the PR first", "do not arm auto-merge until I say" | Peer review and tests approve it. You check the numbers. |
| "Tell me what you did", "send me the values for the scoreboard" | Read GitHub, Dispatch and the numbers; measure a value with a command you run. |
| "Wait for my word before you start", "I tell you when there is space" | State the limit; the agent checks it itself, or a tool does. |
| "If you wait on Sami, tell me and I open the ask" | The agent opens its own Dispatch ask. |
| "Add a guard just in case" | Build what speeds agents up. |

## Sources

- Bezos, [2015 shareholder letter](https://www.sec.gov/Archives/edgar/data/1018724/000119312516530910/d168744dex991.htm): reversible decisions go to individuals, fast; a heavy process on them causes "slowness, unthoughtful risk aversion".
- DORA, [Streamlining change approval](https://dora.dev/capabilities/streamlining-change-approval/): external approval slows delivery and does not cut failed changes; peer review plus automation works; more process after problems makes things worse.
- US Army, [ADP 6-0 Mission Command](https://armypubs.army.mil/epubs/DR_pubs/DR_a/ARN47032-ADP_6-0-000-WEB-1.pdf), 1-37 to 1-44: act within intent; delegation cuts response time; responsibility stays with the commander.
- Google re:Work, [research behind great managers](https://rework.withgoogle.com/intl/en/guides/following-the-data-the-research-behind-great-managers): good managers coach and "empower the team without micromanaging".
- Rother & Liker, [A Kata for PDCA](https://cdn.prod.website-files.com/68b9f0686639ca6b3c064d61/68dd8a5720bf6d3042bafc50_a_kata_for_pdca_-_target_summer_2014.pdf), and [LEI, Kata](https://www.lean.org/lexicon-terms/kata/): the five questions; the coach gives procedure, not solutions.
- Reinertsen, [Product Development Flow, ch. 1](http://lpd2.com/wp-content/uploads/2013/06/ReinertsenFLOWChap1.pdf): queues are the largest hidden cost.
- [Summary of Grove's High Output Management](https://www.becausetech.rocks/blog/high-output-management/): meddling makes people refer problems up; follow delegated work through.
