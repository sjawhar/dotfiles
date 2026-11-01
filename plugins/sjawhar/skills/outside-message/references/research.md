# Researching the reader before you draft

On a repository, read in this order:

- The contributing guide, the issue and pull-request templates, and the code of conduct.
- The last ten issues or pull requests, and how the maintainers write to each other there.
- What has already been discussed about your subject. A question they answered last month, asked again, costs you the reader.

Then follow their conventions in form as well as in words: a pull request on their repository uses their pull-request template, an issue uses their issue template, and a reply in a thread matches that thread.

Off a repository — an email, a Slack message to another company, a release note, a disclosure — read instead:

- Every earlier message between Theorem and this reader, and what they have already been told about this subject.
- The page where they say how they want to be contacted: a security policy, a `SECURITY.md`, a contact page, the project's own disclosure rules.
- How that reader writes: their own release notes, their blog, their messages in the thread you are answering.

Then match the channel: an email answers the email it replies to, and a first contact says in one line who is writing and why.

## A stream's own form rules win

Where the stream that owns the relationship keeps its own form rules — in the project's `AGENTS.md`, its `.claude/skills/` or its brief — those rules decide how the message reads and win over `asd-ste100`, `ce-noslop` and `plain-words`, and some are owned by people through that repository's code owners. Read them before you draft, and send a change to the form there rather than into the skill.

## Where the two steps come from

- **Avoid "we" to an outside reader** — name Theorem or the tool, or write with no actor. Sami asked for this and gave his reason in [OSSV-146 ask 11fc735f](https://dispatch.tail12ec1c.ts.net/issues/OSSV-146/asks/11fc735f-7843-4c7c-bfa7-7512319f0043); his preference, not a measurement.
- **A fresh reviewer reads and cuts** ([OSSV-142 ask 93a2811e](https://dispatch.tail12ec1c.ts.net/issues/OSSV-142/asks/93a2811e-56ab-456e-8c0c-cedfd4ee822b), 2026-10-30). Writing what it removed into the ask is the part that shows the step ran: agents skipped it in 5 of 18 drafts, and an instruction alone did not make it happen.
