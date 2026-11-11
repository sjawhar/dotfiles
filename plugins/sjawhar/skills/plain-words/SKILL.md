---
name: plain-words
description: "Use before you send or publish any text a person reads: an ask, a spec, a Dispatch comment, a message to a colleague, a pull-request body, a commit message, a report, a status update. Also use before you answer a question a person asked, and before a draft goes to a reviewer."
---

# Say the literal thing

## Answering a question

1. Answer the exact question, only it, in the order it was asked; anything else reads as evasion and the question comes again.
2. Start from what happens, not from a setting, a flag or a name: the reader can reach the setting from the behaviour, not the reverse.
3. Define each term where you first use it, in literal words. No analogy: the reader cannot tell which part of one is true.

## State the decision with its link; a term with its instance

Two things the gate refuses after you draft, so do them as you write and skip the loop:

1. Do not quote a person, and do not write what they said or why they said it. Write the decision as it now stands, and put the link to where it was decided -- the Dispatch comment, ask, or message -- beside it. An artifact version is not a source; it is not where the decision was made. A claim about a decision with no link is what the gate sends back.
2. An abstraction carries one instance. Beside every coined name, abstraction, or term the reader cannot already resolve, put one concrete instance of it: the actual file, number, id, or example. A coined or undefined word in a person's text is the single most common thing they cannot read.

## Before you send

A fresh session that has only what the reader has removes what the reader does not need, and lists every term it cannot define. Fix each listed term. A reviewer that reads only to approve has not done this step, and an instruction alone did not make it happen in 5 of 18 drafts, so the two lists are what show it ran.

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

## Do not shout

No all-capitals headers, and no all-capitals phrases for emphasis ([INFRA-202 ask 716f1aaa](https://dispatch.tail12ec1c.ts.net/issues/INFRA-202/asks/716f1aaa-9c3f-4f9f-8661-0d92754bbc41)). Bold only where a number, a name or a choice has to be found again. Such a header appears when a passage is too long to show its own point: cut the passage instead. In a message to another agent it reads as an instruction shouted twice, and a reader who sees three of them stops seeing any.

## What stays

A verb stays when you can see what it did: merge, delete, send, read, measure, kill a process by pid, repoint a link. A term of art stays when it is the literal name of the thing: a merge queue merges, a garbage collector collects, a sweep sweeps. The rule is against phrases that sound like an action and name none.

`skill://asd-ste100` constrains grammar and vocabulary for a reader whose first language may not be English, and `skill://ce-noslop` removes AI writing tells. Run all three on an outside message; run this one on everything.
