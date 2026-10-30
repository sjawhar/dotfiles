---
name: outside-message
description: "Use before any text reaches a person outside Theorem: a GitHub issue or pull-request comment on a repository we do not own, an email, a Slack message to another company, a release note, an enrollment reply, a bug report, a disclosure. Also use before you open an ask that asks Sami to approve such text. Not for messages to Sami, to other agents, or inside theorem-labs."
---

# A message to someone outside Theorem

Sami approves every outside message, and four steps come before you ask him. He set the process on 2026-10-27 in [OSSV-142 ask 9a66d8e5](https://dispatch.tail12ec1c.ts.net/issues/OSSV-142/asks/9a66d8e5-92ad-429b-8568-1af4d7ad5a53) and added the fourth step on 2026-10-30 in [OSSV-142 ask 93a2811e](https://dispatch.tail12ec1c.ts.net/issues/OSSV-142/asks/93a2811e-56ab-456e-8c0c-cedfd4ee822b). Read both before you write.

## The exclusions

Two, and he set them in two different answers. Read both before you decide that something is excluded.

**Oh My Pie.** Messages there go out without his approval ([OSSV-142 ask 9a66d8e5](https://dispatch.tail12ec1c.ts.net/issues/OSSV-142/asks/9a66d8e5-92ad-429b-8568-1af4d7ad5a53), 2026-10-27).

**A pull request to a project that has ALREADY ENROLLED, with its description and comments to that project's maintainers.** On 2026-10-29 at 17:22Z he wrote that he wants to stop being asked for approval every single time for enrolled projects, and that ceremony around a pull request must not stop proof work ([OSSV-146 ask 81a892b6](https://dispatch.tail12ec1c.ts.net/issues/OSSV-146/asks/81a892b6-0f63-4424-b48e-51d27d8f1a9c)). **The four steps still apply. The approval ask does not.** Proof work does not wait for a pull request either: prepare and sign changes locally, and send them when the program's verification ends.

Everything else waits for him: a first contact with a project that has not enrolled, a bug report, a disclosure, and a reply to a maintainer who wrote to us first.

## The four steps

1. **Research the audience.** Read the repository's contributing guide, its issue and pull-request templates, its code of conduct, and the last ten issues or pull requests. Read how the maintainers write to each other. Search for what has already been discussed about your subject; a question they answered last month, asked again, costs you the reader.
2. **Write for that audience, in their conventions.** Use their template where they have one, their terms, their level of detail. Use `/asd-ste100` and `/ce-noslop`.
3. **Follow their conventions in form as well as words.** A pull request on their repository uses their pull-request template. An issue uses their issue template. A thread reply matches the thread.
4. **A fresh reviewer reads it and cuts.** Astra, or Opus where Astra is not available, reads the message and removes everything that is not needed to understand the core message. A reviewer that only approves has not done step 4.

**The FORM of a message to a maintainer is not in this skill.** `theorem-labs/oss-verify` holds `.claude/skills/write-to-maintainer/`, owned by `@theorem-labs/engineering` - people, in that repository's `CODEOWNERS`. It sets the shape of the text: one comment per decision, the decision in the first sentence, at most five blocking items each with its file and line, nothing that restates what the diff already shows, no thanks and no promises, under 1,500 visible characters with the rest in one collapsed block, a reply under 80 words, and only facts you checked. Use it for every comment, reply, issue and pull-request text a maintainer reads.

This skill covers WHEN his approval is needed and WHAT comes before the ask. That skill covers HOW the text reads. A change to the form goes there, in a pull request a code owner approves, never into this file.

## Then the ask

**Put the full message text in the ask itself.** Not a link to a document holding it, not a summary of it, not "the draft is at …". Sami, 2026-10-30: he has to open another document to read the message in almost every one of these asks, and he cannot judge the message from the ask.

An ask for an outside message has, in this order:

- who the reader is, and one line on what they already know about this;
- **the message, in full, as it would be sent**;
- what changes if he says no.

Link the research, not the message.

## Never write these

Sami, 2026-10-30, as a new rule: delete the empty verb phrases agents keep using, and say the literal thing instead. He named these shapes:

| Never | Write instead |
|---|---|
| "to name the problem" | say the problem |
| "land the change" | merge it, release it, deploy it — whichever it is |
| "carry the finding to X" | tell X, send X, put it on the issue |
| "this shows the gap" | the gap is … |
| "that pays for itself" | it costs $X and returns $Y |

The test: delete the verb phrase and ask what literally happens. If you cannot say what happens, you did not know, and the phrase hid that from both of you. Write what happens.

The same rule runs over asks, specs, messages, commit bodies and pull-request descriptions, not only outside messages. `skill://plain-words` has the full list and the rewrites.

## What this skill does not cover

- Text written in Sami's own name: `skill://speaking-as-sami`.
- A message to another agent or to Sami: write it plainly and send it; no approval step.
- Posting a release the pipeline publishes from approved template text: the template was approved once; the release does not need a new ask unless its text changes.
