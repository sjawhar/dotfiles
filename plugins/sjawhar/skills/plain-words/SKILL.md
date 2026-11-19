---
name: plain-words
description: "Use when you write, send or publish text a person reads: an ask, a spec, a Dispatch comment, a message to a colleague, a pull-request body, a commit message, a report, a status update. Also when you answer a question a person asked, and before a draft goes to a reviewer."
---

# Say the literal thing

## Answering a question

1. Answer the exact question, only it, in the order it was asked; anything else reads as evasion and the question comes again.
2. Start from what happens, not from a setting, a flag or a name: the reader can reach the setting from the behaviour, not the reverse.
3. Define each term where you first use it, in literal words. No analogy: the reader cannot tell which part of one is true.

## Decisions and terms

Do both as you write, not after a review sends the draft back:

1. Do not quote a person, and do not write what they said or why. Write the decision as it now stands, with the link to where it was decided (the Dispatch comment, ask or message) beside it. An artifact version is not where a decision was made. AskGate, which reviews each Dispatch write before it posts, flags a text that quotes the person; with its block arm off (`askgate_block` in `omp/extensions/experiments/gates.json`) the write still posts, with the reason as a warning.
2. Beside every coined name, abstraction or term the reader cannot already resolve, put one concrete instance: the actual file, number, id or example. A coined or undefined word is what a reader most often cannot read.

## Before you send

1. Give a fresh session only what the reader has: the draft and what the reader already knows.
2. It removes what the reader does not need, and lists every term it cannot define.
3. Fix each listed term.

A reviewer that reads only to approve has not done this step; the two lists show that it ran.

## The test

Take the verb phrase out and ask what literally happens. Write that. If you cannot say what happens, you did not know, and the phrase hid it from you as well as from the reader.

## The phrases, commonest first

- "land the change", "land the PR": write merge it, release it, deploy it or apply it, whichever it is. If you cannot say which, that is the answer: "it is approved and not merged".
- "unblock the work": X can start once Y merges.
- "name the problem", "name the owner", "name a date": say the problem; who owns it is X; it is due on 11-02. Delete "name": if the sentence then says what happens, keep it that way; if it says nothing, decide the action and write it.
- "it pays for itself", "that pays": it costs $16,306 and returns $60,379 a month.
- "carry it to X", "carry the finding": tell X; send it to X; put it on the issue.
- "this shows the gap", "the number shows": the gap is 12 of 30; the number is 1.17.

## Do not shout

No all-capitals headers, and no all-capitals phrases for emphasis. Bold only where a number, a name or a choice has to be found again. A shouting header appears when a passage is too long to show its own point: cut the passage instead. An agent reads it as an instruction shouted twice, and a reader who sees three of them stops seeing any.

## What stays

A verb stays when you can see what it did: merge, delete, send, read, measure, kill a process by pid, repoint a link. A term of art stays when it is the literal name of the thing: a merge queue merges, a garbage collector collects. The rule is against phrases that sound like an action and name none.

`asd-ste100` constrains grammar and vocabulary for a reader whose first language may not be English, and `ce-noslop` removes AI writing tells. Run all three on an outside message; run this one on everything.
