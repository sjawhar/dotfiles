---
name: plain-words
description: "Use when you write, send or publish text a person reads: an ask, a spec, an issue comment, a message to a colleague, a pull-request body, a commit message, a report, a status update. Also when you answer a question a person asked, and before a draft goes to a reviewer."
---

# Say the literal thing

The rule is against phrases that sound like an action and name none.

**Done when:** "Before you send" ran, as the two lists from its step 2 show; a reviewer that reads only to approve has not done it.

## Answering a question

1. Answer the exact question, only it, in the order it was asked; anything else reads as evasion and the question comes again.
2. Start from what happens, not from a setting, a flag or a name: the reader can reach the setting from the behaviour, not the reverse.
3. Define each term where you first use it, in literal words. No analogy: the reader cannot tell which part of one is true.

## Decisions and terms

Do both in your first draft. Before any review reads it, check that each decision in it has its link and each coined name or term has its instance.

1. Do not quote a person, and do not write what they said or why. Write the decision as it now stands, with the link to where it was decided beside it.
   - Where it was decided is a comment, ask or message on Dispatch, the issue tracker that the `dispatch` command reads and writes. Its link has the form `dispatch://KEY/<comment|ask|message>/<id>`. Outside Dispatch (a pull-request body, a commit message, Slack), give its `https://` link.
   - A version of an artifact (a file or document attached to a Dispatch issue or project, `dispatch://KEY/artifact/<slug>@vN`) is not where a decision was made: link the comment, ask or message instead.
   - **Why:** AskGate flags a text that quotes the person. It reviews each ask, comment, message and document that a `dispatch` command posts, before it posts. With its block arm off (`askgate_block` in `omp/extensions/experiments/gates.json`), the write still posts, with the reason as a warning.
2. Beside every coined name, abstraction or term the reader cannot already resolve, put one concrete instance: the actual file, number, id or example. A coined or undefined word is what a reader most often cannot read.

## Before you send

1. **Start a fresh session.** Give it only what the reader has: the draft and what the reader already knows.
2. **Receive its edited draft and two lists.** It deletes from the draft what the reader does not need, and returns the edited draft with two lists: what it deleted, and every term it cannot define.
3. **Fix the listed terms.** Continue from its edited draft, and fix each listed term.

## The test

Take the verb phrase out and ask what literally happens. Write that. If you cannot say what happens, you did not know, and the phrase hid it from you as well as from the reader.

## The phrases, commonest first

Look for the first phrase first: it is the commonest.

| Phrase | Write |
| --- | --- |
| "land the change", "land the PR" | merge it, release it, deploy it or apply it, whichever it is. If you cannot say which, that is the answer: "it is approved and not merged". |
| "unblock the work" | X can start once Y merges. |
| "name the problem", "name the owner", "name a date" | say the problem; who owns it is X; it is due on 11-02. Delete "name": if the sentence then says what happens, keep it that way; if it says nothing, decide the action and write it. |
| "it pays for itself", "that pays" | it costs $16,306 and returns $60,379 a month. |
| "carry it to X", "carry the finding" | tell X; send it to X; put it on the issue. |
| "this shows the gap", "the number shows" | the gap is 12 of 30; the number is 1.17. |

## Do not shout

- No all-capitals headers, and no all-capitals phrases for emphasis.
- Bold only a number, a name or a choice that the reader must copy or act on after reading, such as a command to run, a deadline or the option to pick.
- In a skill, a bold label may also start a rule.
- **Cut the passage instead of adding a shouting header.** A shouting header appears when a passage is too long to show its own point, that is, when its first sentence does not state the point. Cut such a passage until its first sentence does. An agent reads a shouting header as an instruction shouted twice, and a reader who sees three of them stops seeing any.

## What stays

- Keep a verb when you can see what it did: merge, delete, send, read, measure, kill a process by pid, repoint a link.
- Keep a term of art when it is the literal name of the thing: a merge queue merges, a garbage collector collects.

`asd-ste100` constrains grammar and vocabulary for a reader whose first language may not be English. `ce-noslop` removes AI writing tells. Run all three on an outside message, one that a person outside Theorem reads (`outside-message` defines it). Run this one on everything.
