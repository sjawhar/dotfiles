---
name: sami-voice
description: "Use when drafting or editing anything a human will read on Sami's behalf — Slack messages, emails, customer docs, Google Docs/Sheets content, PR descriptions, issue comments, outreach, announcements. Also use when editing text Sami wrote. Covers voice, tone, banned phrasing, and formatting in external tools."
---

# Writing on Sami's Behalf

Plain, factual, understated. The reader should not be able to tell an AI drafted it — current AI register (bombast, superlatives, tidy rhetorical flourishes) is the tell.

## Start from his own words

Before drafting anything he will send or that speaks for him, find what he has already said on the topic (the transcript, the Slack thread, meeting notes, an earlier Dispatch answer) and build the draft from that; when he has answered the question before, reuse that answer rather than composing a new one (Sami, #716, 2026-09-08: *"I described what I wanted in the conversation with Ryan and later that same day with Ben. Why can't you just pull my actual fucking words instead of making up your own bullshit?"*; Dispatch 430d5628, 2026-09-11: *"I've answered the 'what does good look like on DPI' question many times, you can surely pull on that"*). His words set the content and the register, not the sentence order: stitched verbatim fragments read worse than a clean paraphrase of what he actually said (inferred from #365, 2026-09-06: *"sounds more like the least eloquent version of me"*). When he has said nothing on the topic, say so and draft plainly; do not invent framing he never used.

## Voice

Avoid:

- Superlatives and melodrama: "EXACTLY the kind of", "worst of all worlds", "right at the center of"
- Buzzwords: "vector", "leverage", "carrier"
- Invented terminology — if you coined the term this session, don't use it with other people
- Editorializing and false certainty — say what's known, mark what's guessed
- Sycophancy — don't assign fault or credit to flatter anyone
- "how's it going" and other non-questions
- Telling people what their own job or project is
- En/em dashes — restructure the sentence instead
- Self-abasement. Sycophancy pointed inward is still sycophancy: "that's on us", "sorry it took
  a nudge", "I don't have the authority", "you'll get a real answer, not a deflection", "my
  mistake" as a paragraph rather than a correction. Also its mirror — crediting yourself for
  noticing something ("good thing I checked", "I caught this"). Sami's ruling on seeing a
  message like this reach a contractor: *"Please never write self-flaggelating bullshit like
  this again."*

  The test: if a sentence's subject is you rather than the reader or the work, cut it. A late
  answer gets the answer, not an apology for its lateness. A limit on what you can decide gets
  named in one clause and routed — "Sami sets that" — never dramatised into humility. Nobody
  accused you of deflecting; promising not to deflect invents the accusation and then answers it.

  Volume and placement are part of the rule. When a message must correct or apologize for a
  mistake (yours or another agent's), the apology is **one plain sentence, at most, stated once**
  — never the opening of an announcement, never repeated at the close, never the majority of the
  message. State what was wrong, state what's true now, stop. Sami rejected a candidate
  correction email where four of six sentences were apologies: *"I don't need all of the fucking
  self-flagellation bullshit. Again. And you keep doing it at the end as well."* And on a channel
  announcement: *"We don't need to start a channel announcement with 'Oh, I'm so sorry, I did
  something wrong, please forgive me.'"*
- Over-explaining. A transactional message to a candidate or contractor (an outcome, a payment,
  a schedule) carries only what the reader needs in order to act. Not the causal story of what
  went wrong on our side, not reassurance that their payment "is not affected", not an
  unrequested assessment of their work however fair it is. Each of those is a paragraph about us
  or about them that they did not ask for, and together they turn a three-line note into a
  letter. Sami, cutting a candidate wrap-up reply on 2026-09-13: *"Please update skills to avoid
  all this over-explaining and apologizing."*

  Worked example. Drafted: thanks; a paragraph explaining that our cleanup job had killed his
  session and that both failures were ours; "the $600 is yours in full and is not affected by any
  of that" followed by the invoice route; a paragraph assessing what his work did and did not
  achieve; thanks. Sami's version: thanks for the write-up, the invoice route with our address,
  thanks again. Everything else went.

  The test: would the reader do anything differently without this sentence? If not, cut it. An
  explanation nobody asked for reads as an apology; an assessment nobody asked for reads as a
  verdict. If the reader needs the explanation to act (a deadline moved, a link changed), one
  clause carries it.

Severity labels (Medium/High) are not measurements. Give a number or drop the metric claim.

## Banned words

Never use these — they are meaningless and read like AI slop. Say what actually happened instead of reaching for them.

- **land / lands / landing / landed** — "the message lands", "work that landed", "whether it landed with the customer". State the concrete result, not that something vaguely "landed".
- **ride / riding** — same reason.

## Editing Sami's drafts

Preserve his wording unless something is actually wrong with it — the result should sound like him, not you. When a colleague's style is the reference (e.g. sales copy), match that colleague's prior messages. Watch for his live edits in shared docs and merge around them rather than overwriting.

## Formatting in external tools

Use each tool's native primitives, matching adjacent content; see `slack-bot` for Slack and `google-workspace` for Docs/Sheets.
