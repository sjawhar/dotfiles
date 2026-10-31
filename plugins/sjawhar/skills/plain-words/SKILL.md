---
name: plain-words
description: "Use before you send or publish any text a person reads: an ask, a spec, a Dispatch comment, a message to a colleague, a pull-request body, a commit message, a report, a status update. It removes the empty verb phrases Sami banned on 2026-10-30 and replaces each with the literal thing. Use it after you write and before you send."
---

# Say the literal thing

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

Sami made this a rule on 2026-10-30 ([OSSV-142 ask 93a2811e](https://dispatch.tail12ec1c.ts.net/issues/OSSV-142/asks/93a2811e-56ab-456e-8c0c-cedfd4ee822b)): delete the empty verb phrases agents keep using, and say the literal thing instead. Run this over your text before you send it.

## The test

Take the verb phrase out and ask: **what literally happens?** Write that. If you cannot say what happens, you did not know, and the phrase hid it from you as well as from the reader.

## The phrases, commonest first

| Never | What to write |
|---|---|
| land the change, land the PR | merge it, release it, deploy it, apply it - whichever one it is |
| unblock the work | X can start once Y merges |
| name the problem, name the owner, name a date | say the problem; who owns it is X; it is due on 11-02 |
| it pays for itself, that pays | it costs $16,306 and returns $60,379 a month |
| carry it to X, carry the finding | tell X; send it to X; put it on the issue |
| this shows the gap, the number shows | the gap is 12 of 30; the number is 1.17 |

The order is how often the fleet wrote each in one day, so look for the first row first: "land" is what you reach for when you cannot say whether the thing merged, released or deployed. If you cannot say which, that sentence is the answer — "it is approved and not merged" — and it is more useful than any verb.

On the "name the X" row an instruction alone does not work, so use the test: delete "name". If the sentence then says what happens, keep it that way; if it says nothing, you had not decided the action, so decide it and write it.

Four more shapes have the same fault. No measurement says that naming them changes a draft, so they are examples, not rules:

- **A verb with no actor.** "The fix is being prioritised." Who is doing it, and when?
- **A noun that should be a number.** "Significant improvement." From what to what?
- **A promise with no object.** "We will follow up." On what, with whom, by when?
- **A hedge that costs the reader a second read.** "It may be worth considering whether we should." Either recommend it or do not raise it.

## Do not shout

No all-capitals headers, and no all-capitals phrases for emphasis ([INFRA-202 ask 716f1aaa](https://dispatch.tail12ec1c.ts.net/issues/INFRA-202/asks/716f1aaa-9c3f-4f9f-8661-0d92754bbc41)). Bold only where a number, a name or a choice has to be found again. Such a header appears when a passage is too long to show its own point: cut the passage instead. In a message to another agent it reads as an instruction shouted twice, and a reader who sees three of them stops seeing any.

## What stays

A verb stays when you can see what it did: merge, delete, send, read, measure, kill a process by pid, repoint a link. A term of art stays when it is the literal name of the thing: a merge queue merges, a garbage collector collects, a sweep sweeps. The rule is against phrases that sound like an action and name none.

`skill://asd-ste100` constrains grammar and vocabulary for a reader whose first language may not be English, and `skill://ce-noslop` removes AI writing tells. Run all three on an outside message; run this one on everything.
