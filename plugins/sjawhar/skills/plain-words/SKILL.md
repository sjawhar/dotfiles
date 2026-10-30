---
name: plain-words
description: "Use before you send or publish any text a person reads: an ask, a spec, a Dispatch comment, a message to a colleague, a pull-request body, a commit message, a report, a status update. It removes the empty verb phrases Sami banned on 2026-10-30 and replaces each with the literal thing. Use it after you write and before you send."
---

# Say the literal thing

Sami, 2026-10-30, in [OSSV-142 ask 93a2811e](https://dispatch.tail12ec1c.ts.net/issues/OSSV-142/asks/93a2811e-56ab-456e-8c0c-cedfd4ee822b), made this a new rule: delete the empty verb phrases agents keep using, and say the literal thing instead.

Run this over your text before you send it. It takes a minute and it is not optional.

## The test

Take the verb phrase out and ask: **what literally happens?** Write that. If you cannot say what happens, you did not know, and the phrase hid it from you as well as from the reader.

## The phrases, in the order agents actually use them

Counted in one day's 198,898 assistant messages on this fleet, 2026-10-30:

| Never | Used that day | What to write |
|---|---:|---|
| land the change, land the PR | **1,053** | merge it, release it, deploy it, apply it - whichever one it is |
| unblock the work | 151 | X can start once Y merges |
| name the problem, name the owner, name a date | 23 | say the problem; who owns it is X; it is due on 11-02 |
| it pays for itself, that pays | 13 | it costs $16,306 and returns $60,379 a month |
| carry it to X, carry the finding | 11 | tell X; send it to X; put it on the issue |
| this shows the gap, the number shows | 5 | the gap is 12 of 30; the number is 1.17 |

**The trigger for the commonest one:** you reach for "land" when you cannot say whether it merged, released or deployed. If you can say which, say it. If you cannot, that is the sentence - "it is approved and not merged" - and it is more useful than any verb.

**"name the X" is the one an instruction does not fix.** It survived at the same rate with this skill and without it (11 drafts against 10, over 72). Use the test rather than the row: if deleting "name" leaves a sentence that says what happens, delete it; if it does not, you were about to write a verb for an action you had not decided on.

Three phrases were cut from this table on 2026-10-30 because the same count found zero uses of them in a day: "move the needle", "drive it to zero", "surface the issue". A rule against a phrase nobody writes costs a reader attention and buys nothing.

## Untested, and here because the fault is the same

These were measured at 0 of 72 drafts in both arms, so this skill has no evidence that naming them changes anything. They stay as examples, not as rules:

- **A verb with no actor.** "The fix is being prioritised." Who is doing it, and when?
- **A noun that should be a number.** "Significant improvement." From what to what?
- **A promise with no object.** "We will follow up." On what, with whom, by when?
- **A hedge that costs the reader a second read.** "It may be worth considering whether we should." Either recommend it or do not raise it.

**Where this skill helps, measured:** over 72 drafts of six real status messages, when the writer works from notes it went from 16 of 18 clean to 18 of 18. When the task was already prose, both arms were 18 of 18. So it earns its place where an agent writes up its own work, which is where these phrases live.


## Do not shout

Sami, 2026-10-30, on an ask of mine: "Why do you keep writing your asks in this obnoxious format with all caps headers and stuff?" ([INFRA-202 ask 716f1aaa](https://dispatch.tail12ec1c.ts.net/issues/INFRA-202/asks/716f1aaa-9c3f-4f9f-8661-0d92754bbc41)).

No capitalised headers. No capitalised phrases for emphasis. Bold only where a number, a name or a choice needs to be found again.

The reason it appears: an agent writes a long passage, cannot find its point, and puts a handle on it so a reader can. **The handle is a symptom. Cut the passage instead.** A paragraph that needs a shouted header to be skimmable is a paragraph nobody should have to skim.

The same goes for a message to another agent. A capitalised line in a brief reads as an instruction shouted twice, and a reader who sees three of them stops seeing any.

## What stays

Plain verbs that describe a real action stay: merge, delete, send, read, measure, kill a process by pid, repoint a link. The rule is against phrases that SOUND like an action and name none.

A term of art stays when it is the literal name of the thing: a merge queue merges, a garbage collector collects, a sweep sweeps.

## Where this fits

`skill://asd-ste100` constrains grammar and vocabulary for a reader whose first language may not be English. `skill://ce-noslop` removes AI writing tells. This skill removes verb phrases that carry no fact. Run all three on an outside message; run this one on everything.
