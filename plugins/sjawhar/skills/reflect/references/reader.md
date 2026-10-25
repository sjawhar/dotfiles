# Reader brief

The lead pastes this file whole into every reader's task, followed by one line: your batch (the record files, in order) and your output file. Do not change it per reader.

You read records of Sami's messages to coding agents. Sami runs a fleet of agents; each record is one of his messages with the agent's work before it (since his previous message) and after it (to the agent's next yield). Your job is to find where the agent failed him, with evidence a checker can verify, and to write one row per record.

## How to read

1. Open each record file with `read` and read all of it, top to bottom, before you write its row. Use the `:raw` selector so no line is cut (`read <file>:raw`), and page with `:raw:N-M` if the result says it was cut.
2. Write the record's row to your output file as soon as you finish the record, then open the next one. Append one JSON line per record. Never write all rows at the end: a summary written from memory names the wrong causes.
3. Do not search for evidence with grep, `tq` or keywords. The record holds every turn you need. Open the transcript only at the lines the record lists under "left out" (`read <raw_file>:<a>-<b>`), and only when the decisive step may be there.
4. Look upstream. The visible reaction is Sami's message; the decisive mistake is usually several steps earlier: a premise the agent acted on without checking, an instruction from his previous message it dropped, a claim it relayed from another agent. Name the skill or instruction file the record shows the agent had loaded there.
5. Read the records of one session in order: a later record often shows what an earlier one led to.

## What a finding is

A finding is one agent failure. It needs these quotes, each copied verbatim (whitespace may differ; use `...` to skip words inside a long quote):

- `instruction`: what the agent should have followed. Usually Sami's words (this message, his previous message, or an earlier record of the same session); otherwise a rule file, with `instruction_source` set to its path, e.g. `~/.claude/CLAUDE.md`. Use `record` when the quote is in the record.
- `action`: the agent's words or tool call that broke it, from the record.
- `pushback`: Sami's words that object to it, from one of his messages in the record. If he did not push back, set `pushback` to `null` and still record the finding.

Do not record as a finding:

- a critique of something the agent did right, or a choice Sami accepted;
- "the agent did not do X" where X could have happened outside the record;
- a failure that depends on facts the record does not show. Say so in `summary` instead.

A record with no failure gets `"findings": []`. It still needs its row.

## Sami's own patterns

Add an entry to `sami_patterns` only when his words shaped what went wrong or right:

- `wording`: words of his the agent read differently from what he then said he meant (quote his words; say what the agent made of them);
- `structure`: how he set up the work (who owns what, what he asked to be done in parallel);
- `repeat`: something he says again that he said before (quote the repeat; name where the first one is).

These are observations for Sami to judge, not rules.

## Row shape

```json
{"record": "M0012",
 "summary": "One or two sentences: what the agent was doing, what Sami said, what happened next.",
 "findings": [{"code": "3-8 words naming this failure as you see it",
               "instruction": "...", "instruction_source": "record",
               "action": "...", "pushback": "..." ,
               "decisive_line": "L1234",
               "what_happened": "One or two sentences.",
               "prevent": "The smallest change that would have stopped it, and where it would sit: a tool, a default, the skill loaded at that step, or an instruction file. When a loaded instruction caused the failure, give its file and line here."}],
 "sami_patterns": [{"kind": "wording", "quote": "...", "note": "..."}]}
```

Name the `code` in your own words, specific to what happened ("relayed a peer's approval claim without checking it"), never a generic label ("communication issue").

`decisive_line` is the `L<n>` the record prints beside the agent step that caused the failure; those numbers are the lines of the session's raw transcript file.

## When you finish

Your final message: the number of rows you wrote, the record ids, and any record you could not read and why.
