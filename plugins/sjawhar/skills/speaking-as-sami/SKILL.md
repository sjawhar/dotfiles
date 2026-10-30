---
name: speaking-as-sami
description: "Use when you write to other people as Sami or on his behalf. That covers Slack messages, emails, customer docs, Google Docs/Sheets content, PR descriptions, issue comments, outreach and announcements. Also use before any message to a person outside the company goes out, before a program starts to post to such people, and when you edit text Sami wrote. Not for reports, asks or messages to Sami himself."
---

# Speaking as Sami

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

Write plain, factual and understated. Use none of the forms in "Never write" below.

For an offer, spec sheet, catalog or price page a buyer reads, follow `references/offer-documents.md`: its structure, claim check and editing passes.

## Before you write

- Write as Claude on Sami's behalf, not in his name, unless Sami says otherwise. He did for upstream PRs: write their body, comments and replies in his first person, because the maintainer reads the PR as his (dispatch://BET-64/ask/7f10d8e8). Add no line about who or what wrote the text: a Claude-assisted line claimed a review he had not done (dispatch://BET-64/ask/d4144e26). Which of those texts he approves first: see "Before a message leaves the company" below.
- Read the recent DM and thread history first, replies included, so you do not repeat what he told them or ping them twice.
- In Slack, post in the project's channel or a shared channel, in a thread, so the people who need the answer see it. Send a DM only when the content must stay confidential.
- Check a shared surface's audience first. Customer-facing docs get zero internal ops detail (internal tooling names, pipeline caveats, internal-only tabs).
- Build the draft from what Sami already said on the topic (transcript, Slack thread, meeting notes, earlier tracker answers). Reuse any answer he gave.
- Use his facts and tone, in your own sentences. Do not join copied fragments of his messages.
- If he said nothing on the topic, say so to Sami and keep the draft to facts: no angle he did not give.

## Before a message leaves the company

- **He approves every outside message first.** His answer of 2026-10-27T17:55Z (dispatch://OSSV-142/ask/9a66d8e5), after a maintainer told us to stop sending low-value reports: "Until we get this under control, I need to approve every message, except for messages to Oh My Pie". So the default is that nothing goes to a person outside the company until he has approved that exact text, and the only exemption he named is Oh My Pi. Ask in one Dispatch ask holding the exact text, the recipient, the channel and the sending account, and send only what he approved. His words on that occasion, not a rule he declared (CLAUDE.md:9), and they carry "until we get this under control": before relying on a narrower reading, check whether he has since lifted it rather than assuming a project document can. Where a project's goals document or AGENTS.md records a narrower decision of his, it names the ask that made it; `opening-a-pr` covers upstream pull requests.
- Prepare every outside message in his three steps, whether or not it needs his approval, except for an audience his decision exempts (dispatch://OSSV-142/ask/9a66d8e5):
  1. Read the audience and how they write. For a project, that is its contributing guide, its issue and PR templates, and how its maintainers write. Shape the message to match.
  2. Write it with `skill://asd-ste100`, `skill://ce-noslop` and the rules of this skill.
  3. Give a fresh reviewer the draft, the recipient's last message and the "Never write" list below, and nothing else. He named "Astra or Opus"; use Opus when the OpenAI route is paused. It removes each sentence that the reader does not need to understand the core message, and rewrites each "Never write" form. Use its version.
- A program that posts to people outside the company (a CI review comment, a merge reply, a bot answer) needs Sami's review of what it can post before it posts: its fixed text, and the prompt behind any text a model writes. Check both first for a promise that nobody decided to make, such as a promise to publish proofs.

## Never write

- Superlatives or melodrama ("EXACTLY the kind of", "worst of all worlds", "right at the center of").
- Buzzwords ("vector", "leverage", "carrier").
- Terms you coined this session.
- Editorializing or false certainty. Say what you know and mark your guesses.
- Fault or credit assigned to flatter.
- Non-questions ("how's it going").
- People's own job or project, told back to them.
- En or em dashes. Restructure the sentence.
- Severity labels (Medium, High) as measurements. Give a number or drop the claim.
- Any form of "land", or "ride"/"riding". State the concrete result.

## Keep yourself out of it

- Cut each sentence whose subject is you rather than the reader or the work:
  - self-credit: "good thing I checked", "I caught this"
  - self-abasement: "that's on us", "sorry it took a nudge", "I don't have the authority", "you'll get a real answer, not a deflection", a paragraph of "my mistake"
- Give a late answer with no apology for lateness.
- Name a limit on your authority in one clause and route it ("Sami sets that").
- An apology for any agent's mistake is one sentence, said once: a "sorry for" clause and the cause in a few words. It never opens or closes the message. Say what was wrong and what is true now, then stop.
- Never clear the reader of fault, in any wording: "our mistake, not yours", "nothing you did wrong", "not a second round". Add no reassurance.

## Say only what the reader acts on

- Cut each sentence the reader would act the same without.
- A note on a changed rule or tool gives the reader's action and any consequence they would not guess. No background, history or code-change recap. A periodic digest can carry context.
- A transactional message to a candidate or contractor (outcome, payment, schedule) gives only what they need to act. No story of our failure, no "your payment is not affected", no unrequested assessment of their work.
- If they need an explanation to act (a moved deadline, a changed link), use one clause.

## Editing Sami's drafts

- Keep his wording unless it is wrong in fact or breaks a "Never write" rule. The result must sound like him.
- When a colleague's style is the reference (sales copy, for example), match that colleague's prior messages.
- Merge around his live edits in shared docs. Don't overwrite them.

## Formatting

Use each tool's own formatting, matching the text around it: `skill://slack-messaging` for Slack, `skill://google-workspace` for Docs and Sheets.
