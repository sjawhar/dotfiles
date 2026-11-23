---
name: outside-message
description: "Use when text goes to someone outside Theorem: a GitHub issue or pull-request comment an outside person reads, an email, another company's Slack, a release note, an enrollment reply, a bug report, a disclosure. Also before asking Sami to approve it. Not for Sami, agents or Theorem staff."
---

# A message to someone outside Theorem

Sami approves every outside message by default.

A kind of message stops needing his approval once a skill or template guides it and he has seen how the reader's project reacts. What decides is whether the message is a vetted routine or novel contact, not who the reader is.

An outside message is one a person outside Theorem reads, not one that is only public. A reply in a bot-opened thread on a pull request Theorem authors is public, so the public leak check covers it (`public-leak-check.sh check-public-text <file>`).

| A reply in a bot-opened thread on | Read by | Outside message |
| --- | --- | --- |
| A pull request Theorem authors | No outside person | No |
| A contributor's pull request | That contributor | Yes |

A narrower decision of his, where one exists, is recorded in the project's AGENTS.md or goals document with the ask that made it. Do not assume one without that record. `opening-a-pr` covers upstream pull requests.

## Oh My Pi

Messages to Oh My Pi (`can1357/oh-my-pi`), on our pull requests and on contributors', need neither his approval nor the two steps below. Run the public leak check on them, since these comments are public. A fresh cut by a session that did not write the text is recommended, not a gate.

The pi-envoy plugin's own `outside-message` skill states this exception only generically ("a project the operator has excluded by name"); the project name lives here on purpose.

## Goes without his approval

For any other project, all three hold:

- It is a captured routine: a skill or template guides it. The message is that template with only the names, links and numbers that differ per send changed. Any other change departs from it.
- The two steps below ran: the writing skills passed over the text, and a fresh reviewer cut the exact text that goes out.
- It sits in a flow we control: an enrollee who came to us, or a project already enrolled; not contact we open into a space we do not run. A new enrollee counts with no prior rapport: the control is the template and the flow.

| Captured routine | Its reader |
| --- | --- |
| A pull request and its maintainer comments (`upstream-bugfix-patch` holds the form and timing of a finding or patch to an already-enrolled project) | An already-enrolled project's maintainers |
| The enrollment thank-you and the first "we will start reviewing your PR" note | A new enrollee |

The template is approved once. Such a send to the reader its routine names needs no new ask.

Enrolled means the enrolling project's own record lists the project. The enrolling project's stream names where that record is in its brief. With no record found, the project has not enrolled. Do not decide it from a message's tone or from a maintainer having replied before.

| Term | Meaning |
| --- | --- |
| Stream | The line of work with its own lead and its own stream issue |
| Stream issue | The open issue labelled `stream` in Dispatch |
| Dispatch | The issue tracker the `dispatch` command reads |
| Brief | The standing instructions the stream's agents work from |

## Waits for him

Any one of these, except for Oh My Pi:

- Novel contact into a space we do not run: a first contact, or any contribution, to a project that has not enrolled, until Sami moves it to "no approval" (below).
  - Send such a project one pull request, and no second one until Sami has seen how its maintainers reacted to the first.
  - Put that reaction, or that they have not reacted yet, in the ask for the second.
- No skill or template yet, or a send that departs from its template.
- It commits him or represents his position: a promise, a deadline, a disclosure, a claim he would have to stand behind.
- A program that will post to people outside the company (a CI review comment, a merge reply, a bot answer).
  - He reviews what it can post before it posts (to Oh My Pi too), its fixed text and the prompt behind any text a model writes.
  - Check both first for a promise nobody decided to make, such as a promise to publish proofs.

A case between the two lists waits. The ask for such a case says which way it leaned and why.

A project we do not run that has not enrolled starts at "approve each". It moves to "no approval" for the captured routines only when Sami says so, once per project. Record his call where the project's stream keeps its enrollment record, so no agent promotes a project on its own judgement. A new enrollee or an enrolled project does not start there: its captured routines go without approval (above).

## The two steps

1. Research the reader, then write in their conventions.

   | Where | Read | Write |
   | --- | --- | --- |
   | On a repository | Its contributing guide, issue and pull-request templates and code of conduct; its last ten issues or pull requests and how the maintainers write there; and what was already discussed about your subject | Use their pull-request template for a pull request and their issue template for an issue. Match the thread for a reply. |
   | Off a repository (an email, a Slack message to another company, a release note, a disclosure) | Every earlier message between Theorem and this reader; the page where they say how to contact them (a security policy, `SECURITY.md`, a contact page, disclosure rules); and how they write (release notes, blog, their messages in the thread) | An email answers the email it replies to. A first contact says in one line who is writing and why. |

   - Where the stream that owns the relationship keeps form rules (in its project's `AGENTS.md`, `.claude/skills/` or brief), those rules decide how the message reads, over the writing skills below.
   - Send a change to the stream's form there, not into this skill.
   - Run `asd-ste100`, `ce-noslop` and `plain-words` over the text.
   - Avoid "we" (Sami's preference): name Theorem or the tool, or write the sentence with no actor.
2. A fresh reviewer cuts it.
   - Give a session that did not write the message (the `astra` agent, or an Opus model where it is unavailable) the draft, the recipient's last message and, for text in Sami's name or on his behalf, the "Never write" list of `speaking-as-sami`, and nothing else.
   - It removes everything the reader does not need to understand the core message.
   - It rewrites each "Never write" form.
   - Use its version.
   - A reviewer that reads only to approve has not done this step.

The text he approves, or that goes out, is the text the reviewer cut, byte for byte. Any edit after the cut, even one line of fact, goes back through step 1 and a new review.

## Then the ask

Put the full message text in the ask itself, not a link, a summary or "the draft is at ...": Sami cannot judge a message he has to open another document to read. In this order:

1. Who the reader is, with one line on what they already know; the channel and the sending account.
2. The message, in full, as it would be sent.
3. What the reviewer removed, in three lines or fewer, or "the reviewer removed nothing": the list shows the step ran.
4. What changes if he says no.

Link the research, not the message. Send only the text he approved.

## What this skill does not cover

| Case | What applies |
| --- | --- |
| How the text reads | `plain-words` for empty verb phrases, `speaking-as-sami` for text in Sami's name |
| A message to another agent or to Sami | Write it plainly and send it; no approval step |
| Sami's personal admin (sellers, vendors, couriers, taskers, family) in the omp `personal` profile | `contacting` holds its identity and approval rules |
