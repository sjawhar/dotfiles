---
name: plain-words
description: "Use before you send or publish any text a person reads: an ask, a spec, a Dispatch comment, a message to a colleague, a pull-request body, a commit message, a report, a status update. It removes the empty verb phrases Sami banned on 2026-10-30 and replaces each with the literal thing. Use it after you write and before you send."
---

# Say the literal thing

Sami, 2026-10-30, in [OSSV-142 ask 93a2811e](https://dispatch.tail12ec1c.ts.net/issues/OSSV-142/asks/93a2811e-56ab-456e-8c0c-cedfd4ee822b), made this a new rule: delete the empty verb phrases agents keep using, and say the literal thing instead.

Run this over your text before you send it. It takes a minute and it is not optional.

## The test

Take the verb phrase out and ask: **what literally happens?** Write that. If you cannot say what happens, you did not know, and the phrase hid it from you as well as from the reader.

## The phrases he named, with their replacements

| Never | What to write |
|---|---|
| name the problem, name the owner, name a date | say the problem; who owns it is X; it is due on 11-02 |
| land the change, land the PR | merge it, release it, deploy it, apply it — whichever one it is |
| carry it to X, carry the finding | tell X; send it to X; put it on the issue |
| this shows the gap, the number shows | the gap is 12 of 30; the number is 1.17 |
| it pays for itself, that pays | it costs $16,306 and returns $60,379 a month |
| drive it to zero | get it to zero by Friday, by doing Y |
| move the needle | the number goes from 24 to 25 |
| unblock the work | X can start once Y merges |
| surface the issue | tell the owner; write it on the issue |

## The same fault in other clothes

These hide the same emptiness and go the same way:

- **A verb with no actor.** "The fix is being prioritised." Who is doing it, and when?
- **A noun that should be a number.** "Significant improvement." From what to what?
- **A promise with no object.** "We will follow up." On what, with whom, by when?
- **A hedge that costs the reader a second read.** "It may be worth considering whether we should." Either recommend it or do not raise it.

## What stays

Plain verbs that describe a real action stay: merge, delete, send, read, measure, kill a process by pid, repoint a link. The rule is against phrases that SOUND like an action and name none.

A term of art stays when it is the literal name of the thing: a merge queue merges, a garbage collector collects, a sweep sweeps.

## Where this fits

`skill://asd-ste100` constrains grammar and vocabulary for a reader whose first language may not be English. `skill://ce-noslop` removes AI writing tells. This skill removes verb phrases that carry no fact. Run all three on an outside message; run this one on everything.
