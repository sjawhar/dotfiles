---
name: outside-message
description: "Use before any text reaches a person outside Theorem: a GitHub issue or pull-request comment on a repository we do not own, an email, a Slack message to another company, a release note, an enrollment reply, a bug report, a disclosure. Also use before you open an ask that asks Sami to approve such text. Not for messages to Sami, to other agents, or inside theorem-labs."
---

# A message to someone outside Theorem

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

Sami approves every outside message ([OSSV-142 ask 9a66d8e5](https://dispatch.tail12ec1c.ts.net/issues/OSSV-142/asks/9a66d8e5-92ad-429b-8568-1af4d7ad5a53), 2026-10-27), except in the two cases below.

## The two exclusions

Read both answers before you decide a message is excluded. Neither follows from the shape of a message.

- **One named project** goes out without his approval; the answer above names it.
- **A pull request to a project that has already enrolled**, with its description and its comments to that project's maintainers, needs no approval ask ([OSSV-146 ask 81a892b6](https://dispatch.tail12ec1c.ts.net/issues/OSSV-146/asks/81a892b6-0f63-4424-b48e-51d27d8f1a9c), 2026-10-29): he asked to stop being asked for approval every single time for enrolled projects. Say so when you are told to write one, and write the message itself instead. Both steps below still apply; only the ask does not. The work does not wait for the pull request either: prepare and sign the changes locally, and send them when the work they belong to ends. Enrolled means the enrolling project's own record, and its stream's brief says where that record is; do not decide it from a message's tone or from a maintainer having replied to us before.

Everything else waits for him: a first contact with a project that has not enrolled, a bug report, a disclosure, and a reply to a maintainer who wrote to us first.

## The two steps

1. **Research the audience, and write in their conventions** — their template, their terms, their level of detail, their thread. `references/research.md` lists what to read before you draft. Run `skill://asd-ste100`, `skill://ce-noslop` and `skill://plain-words` over the text. Where the stream that owns the relationship keeps its own form rules, in the project's `AGENTS.md`, its `.claude/skills/` or its brief, those rules decide how the message reads and win over the three skills above, and some are owned by people through that repository's code owners. Read them before you draft, and send a change to the form there rather than into this skill.
2. **A fresh reviewer reads it and cuts** ([OSSV-142 ask 93a2811e](https://dispatch.tail12ec1c.ts.net/issues/OSSV-142/asks/93a2811e-56ab-456e-8c0c-cedfd4ee822b), 2026-10-30). Astra, or Opus where Astra is not available, reads the message in a session that did not write it and removes everything a reader does not need to understand the core message. A reviewer that reads only to approve has not done this step. **Write in the ask what it removed**, in three lines or fewer, or "the reviewer removed nothing". Agents skipped this step in 5 of 18 drafts, and an instruction alone did not make it happen, so the list is what shows it ran.

## Then the ask

**Put the full message text in the ask itself.** Not a link to a document holding it, not a summary, not "the draft is at …": Sami cannot judge a message he has to open another document to read.

An ask for an outside message has, in this order: who the reader is, and one line on what they already know; the message, in full, as it would be sent; what the reviewer removed; what changes if he says no. Link the research, not the message.

## What this skill does not cover

- How the text reads: `skill://plain-words` for empty verb phrases, `skill://speaking-as-sami` for text in Sami's own name.
- A message to another agent or to Sami: write it plainly and send it; no approval step.
- A release the pipeline publishes from approved template text: the template was approved once, so the release needs no new ask unless its text changes.
