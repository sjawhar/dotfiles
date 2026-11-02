---
name: outside-message
description: "Use before any text reaches a person outside Theorem: a GitHub issue or pull-request comment on a repository we do not own, an email, a Slack message to another company, a release note, an enrollment reply, a bug report, a disclosure. Also use before you open an ask that asks Sami to approve such text. Not for messages to Sami, to other agents, or inside theorem-labs."
---

# A message to someone outside Theorem

Sami approves every outside message by default ([OSSV-142 ask 9a66d8e5](https://dispatch.tail12ec1c.ts.net/issues/OSSV-142/asks/9a66d8e5-92ad-429b-8568-1af4d7ad5a53), 2026-10-27). The lockdown is on because outside messages were low quality, and it loosens as each routine is captured in a skill or template and a party's reaction is gauged ([OSSV-271 ask c75d17dd](https://dispatch.tail12ec1c.ts.net/issues/OSSV-271/asks/c75d17dd-bb18-4cb3-be3c-957da82655be), 2026-11-01). The test is not who the reader is but whether the message is a vetted routine or novel contact.

## Goes without his approval

All three hold:

- **A routine with a skill or template behind it** (`references/approval.md` lists the ones there are). A formulaic message with nothing guiding it is not yet a routine, and waits.
- **The process below ran** — the scales passed and a fresh reviewer cut the exact text that goes out; "followed the process" means those happened, not that someone read it.
- **It sits in a flow we control** — an enrollee who came to us, a project already enrolled — not contact we open into a space we do not run.

A new enrollee counts even with no prior rapport: the control is the template and the flow, not standing with the party.

## Waits for him

Any one:

- **Novel contact into a space we do not run** — a first contact, or the early contributions, to a project that has not enrolled. Do not send 13 pull requests into a cold repository before he has gauged how its maintainers react.
- **No skill or template yet**, or a message that is not that template sent largely unchanged.
- **It commits him or represents his position** — a promise, a deadline, a disclosure, a claim he would have to stand behind.
- Between the two it waits, and the ask says which way it leaned and why. A new project moves from "approve each" to "no approval" only when Sami says so (`references/approval.md`).

## The two steps

1. **Research the audience, and write in their conventions** — their template, their terms, their level of detail, their thread (`references/research.md`). Run `skill://asd-ste100`, `skill://ce-noslop` and `skill://plain-words` over the text. **Avoid "we" in text to an outside reader**: name Theorem or the tool, or write the sentence with no actor (`references/research.md` for his reason).
2. **A fresh reviewer reads it and cuts.** Astra, or Opus where Astra is not available, reads the message in a session that did not write it and removes everything a reader does not need. A reviewer that reads only to approve has not done this step. **Write in the ask what it removed**, in three lines or fewer, or "the reviewer removed nothing": the list is what shows the step ran (`references/research.md`).

**The text he approves is the text the reviewer cut, byte for byte.** Any edit after the cut, even one line of fact, sends the text back through step 1 and a new fresh review. On 2026-11-02 two sentences added after the cut reached his ask, and a second cut removed one as inaccurate ([INFRA-263 comment 400ad213](https://dispatch.tail12ec1c.ts.net/issues/INFRA-263/comments/400ad213-e015-49b6-a726-aa5444a3100c)).

## Then the ask

**Put the full message text in the ask itself.** Not a link to a document holding it, not a summary, not "the draft is at …": Sami cannot judge a message he has to open another document to read.

An ask for an outside message has, in this order: who the reader is, and one line on what they already know; the message, in full, as it would be sent; what the reviewer removed; what changes if he says no. Link the research, not the message.

## What this skill does not cover

- How the text reads: `skill://plain-words` for empty verb phrases, `skill://speaking-as-sami` for text in Sami's own name.
- A message to another agent or to Sami: write it plainly and send it; no approval step.
