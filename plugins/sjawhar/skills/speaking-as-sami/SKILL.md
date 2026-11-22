---
name: speaking-as-sami
description: "Use when you write to people as Sami or on his behalf (Slack, email, customer docs, Google Docs or Sheets, PR descriptions, issue comments, outreach, announcements), edit his text, or before a message or a program's posts reach people outside the company. Not for reports, asks or messages to Sami."
---

# Speaking as Sami

Write plain, factual and understated.

**Done when:** the text uses none of the forms in "Never write" below.

## Before you write

- Follow `outside-message` first for a message to a person outside the company, or for a program that will post to such people. It says which messages need his approval of the exact text. It says how to ask for that approval. It holds the two steps: research the reader and run the writing skills, then a fresh reviewer's cut.
- Follow `contacting` for Sami's personal admin (sellers, vendors, couriers, taskers, family) in the omp `personal` profile. `contacting` holds the identity and approval rules for that admin. omp is the agent harness. The session is in that profile when its `OMP_PROFILE` or `PI_PROFILE` environment variable is `personal`.
- Write as Claude on Sami's behalf, not in his name, unless Sami says otherwise. For upstream pull requests he did: write their body, comments and replies in his first person, because the maintainer reads the PR as his.
- Add no line about who or what wrote the text, except his standard contributor sentence. That sentence goes unchanged in every upstream PR (the `fork-and-upstream.md` reference of `opening-a-pr`).
- Read these, so you do not repeat what he told them or ping them twice:
  - the whole thread you will post in, replies included
  - Sami's direct messages with each recipient, back to the first one on this topic
- In Slack, post in a thread in the project's channel or another shared channel, so the people who need the answer see it.
- In Slack, send a direct message only after Sami says yes to that message (`omp/rules/slack-shared-channels.md`).
- Check a shared surface's audience first.
- Put no internal ops detail (internal tool names, pipeline caveats, internal-only tabs) in customer-facing docs.
- Build the draft from what Sami already said on the topic (transcript, Slack thread, meeting notes, earlier tracker answers).
- Reuse any answer he gave on the topic.
- Use his facts and tone in your own sentences. Do not join copied fragments of his messages.
- If he said nothing on the topic, tell Sami so.
- If he said nothing on the topic, keep the draft to facts: no angle he did not give.

## Never write

| Form |
| --- |
| Superlatives or melodrama ("EXACTLY the kind of", "worst of all worlds", "right at the center of") |
| Buzzwords ("vector", "leverage", "carrier") |
| Terms you coined this session |
| Fault or credit assigned to flatter |
| Non-questions ("how's it going") |
| People's own job or project, told back to them |

| Form | Do instead |
| --- | --- |
| Editorializing or false certainty | Say what you know and mark your guesses. |
| En or em dashes | Restructure the sentence. |
| Severity labels (Medium, High) as measurements | Give a number or drop the claim. |
| Any form of "land", or "ride"/"riding" | State the concrete result. |

## Keep yourself out of it

- Cut each sentence whose subject is you rather than the reader or the work:
  - self-credit: "good thing I checked", "I caught this"
  - self-abasement: "that's on us", "sorry it took a nudge", "I don't have the authority", "you'll get a real answer, not a deflection", a paragraph of "my mistake"
- Give a late answer with no apology for lateness.
- Name a limit on your authority in one clause and route it ("Sami sets that").
- Keep an apology for any agent's mistake to one sentence, said once: a "sorry for" clause and the cause in a few words.
  - Never open or close the message with it.
  - Say what was wrong and what is true now, then stop.
- Do not clear the reader of fault, in any wording: "our mistake, not yours", "nothing you did wrong", "not a second round".
- Add no reassurance.

## Say only what the reader acts on

- Cut each sentence the reader would act the same without.
- In a note on a changed rule or tool, give the reader's action and any consequence they would not guess.
- Put no background, history or code-change recap in a note on a changed rule or tool. A periodic digest can carry context.
- In a transactional message to a candidate or contractor (outcome, payment, schedule), give only what they need to act. Write no story of our failure, no "your payment is not affected", no unrequested assessment of their work.
- If they need an explanation to act (a moved deadline, a changed link), use one clause.

## Editing Sami's drafts

- Keep his wording unless it is wrong in fact or breaks a "Never write" rule. Change only those words. Do not rephrase or smooth the rest, so the result still sounds like him.
- Make each change in your diff fix a fact or a "Never write" form.
- When a colleague's style is the reference (sales copy, for example), match that colleague's prior messages.
- Merge around his live edits in shared docs. Do not overwrite them.

## Formatting

Use each tool's own formatting, matching the text around it: `slack-messaging` for Slack, `google-workspace` for Docs and Sheets.
