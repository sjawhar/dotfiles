---
name: updating-docs
description: "Use when creating or updating documentation, READMEs, AGENTS.md files, skills, runbooks, code comments, or any prose that describes how a system works. Also use whenever a change touches documented behavior, even if the user asked only for the code change."
---

# Updating Docs

Describe current behavior, in code comments too: readers act on the doc, and git log is the changelog.

**Done when:** after an edit, you have read the whole document, not a grep for what you added. You read every line, in one read or in ranges that cover it. You checked two things: every part of the edit landed where you meant it, since an insert can take the wrong anchor, and the untouched neighbors survived.

A skill's own limits, structure and tests are in `writing-agent-skills`.

## Write what is true now

- Delete on sight PR or issue breadcrumbs ("as of #1234"), migration trails ("previously X, now Y"), "NEW:" or "UPDATED:" markers and dated notes: readers act on the doc, and git log is the changelog.
- Keep provenance (who, when, the incident) in the commit message or PR body.
- Outside agent guides and skills, a rule may carry a one-line citation (a link, `file:line`), never a dated story, tally or justifying quote.
- Record what changed in the commit message, not in a note beside the corrected text: readers take the note as current.

## The test for every line

Keep a line of an `AGENTS.md`, `CLAUDE.md` or skill file only if all three hold:

1. A competent engineer new to the repository would get the task wrong, or lose 10 minutes or more, without it.
2. They cannot find it in the code, `--help`, a test, or a docs page the doc links.
3. It is true at main today.
   - Check each path, command, flag and name against the checkout.
   - Correct or drop a false line.

Remove always:

- History: dates, incidents, measurements of past events, who decided what, issue keys, links to decisions, pull request numbers as provenance.
  - History goes in the commit message or PR body; git keeps the removed text.
- Duplicates: a fact lives in one doc, and others link to it.
- Restatements of what code, a test or a linter enforces, for the same reason.
- Long explanations of why.
  - Keep one clause where a rule would otherwise look arbitrary.
- Emphasis as force: NEVER or MUST in capitals, "critical", bold.
  - A bold label that starts a rule in a skill is a label, not force.
  - A rule that needs force is a hook or a check.

Move a fact to a README or `docs/` page only when that page is where an engineer would look and no page holds it yet; otherwise delete it.

## An agent guide (AGENTS.md, CLAUDE.md)

| Guide | Lines, at most | Bytes, at most |
| --- | --- | --- |
| Root | 150 | 10 KB (10,240) |
| Package (`<dir>/AGENTS.md`) | 80 | 6 KB (6,144) |

- Check with `wc -l -c`.
- Shorter is better.
- A package with nothing non-obvious gets a five-line guide or none.
- Shape: one to three lines on what the package is and who uses it; the exact commands to build, test (one test too), lint and run, each checked; the non-obvious conventions and traps; links to docs pages for depth.
- Plain lists and short lines; no table of every file, no API reference, no release history.
- The root guide holds repository-wide facts and links each package guide.
- The root guide does not restate a package's details.
- Before you rename a heading or a guide, `git grep` for it: code comments cite guide headings and paths by name.

## Write a rule

- State what the rule requires and what it does not cover, or agents apply it past its edge.
- Delete a committed policy (gating file, threshold, process doc) when no commit message or PR body records what Sami said to create it, and when.
- State the exception of a rule that has one, or an agent that meets it drops the whole rule.
- Write "always" or "never" only for a rule with no exception, for the same reason.
- Before you add a sentence, correct the existing one that is wrong, incomplete or ambiguous: a correction appended beside it leaves both readings.
- Delete or narrow the sentence that caused the failure, for the same reason.

## Correct an overturned belief

- When the old way is gone, delete the warnings against it: a warning keeps the dead option alive.
- When the old way is gone, describe the correct way, for the same reason.
- Correct every copy in place (headings, titles, flags, acceptance rows, downstream steps): a correction under a heading that still asserts the old model has not landed.
- Besides a grep, also list and check each file, flag, test or step that assumed the old state: grep misses a copy that shares no words with the claim.
- Apply the corrected rule to the case that prompted it; do not only restate it.
- Mark each claim you did not observe as inferred: a correction's overreach spreads faster than the original error.
- When a decision says what code may accept, make the code refuse the rest in the same change; a spec alone changes nothing it accepts.

## On a cutover, sweep the changed directory

A cutover replaces a name, path, command, tool or interface with a new one and removes the old one: a rename, a move, a migration.

On a cutover, **read `references/cutover-sweep.md` now** for the sweep's steps.

## Read the whole artifact back

- After an edit to a file under version control, also read its diff (`git diff -- <file>` or `jj diff <file>`): it must change no line you did not mean to change.
- Before you trust a read-back, make it find a string you know is there, so a broken reader cannot report success.
- Apply `(start, end, text)` edits back to front, or replace the region in one slice: a change to a string invalidates offsets computed on it.
  - After either, check the result.
- Chain a publish command to its guard with `&&`, or start the script with `set -euo pipefail`: a newline is not a guard.

## Claims in prose

- Measured X only on A? Write "A: X; B: not checked", not "A and B: X".
- Paraphrase a result from the run's own summary line, with every count it prints.
- A claim about one test needs that test's own record.
- Before you describe a format, print one instance verbatim.
- When another PR, branch or system that prose describes changes, correct the text in place: prose owns only what its own diff makes true.
- Docstrings of tests and witnesses (code that demonstrates a claim) state only what their assertions check.
- When you sweep prose for stale claims, include the docstrings too.

## Integrate, don't accrete

- Before you add a section, look for one on the topic: patched-in sentences are how two sections come to disagree.
- If a section's heading names the topic, or it already states half or more of what you would add, rewrite that section, for the same reason.
- Merge per-case sections (per provider, per version) that repeat each other into one that names the differences.
- A consolidation leaves the doc shorter.
- Rewrite a doc that grows on every edit, even simplifying ones: it is accreting.
- Keep one doc per audience or purpose.
  - Merge duplicate files.
  - State in the PR body why no existing file can hold a new file's content.

## A rule agents know but keep forgetting

- Stop rewording it.
- Build a refusal or an automatic step for it at the point of action.
- Check who a mechanism reaches: it binds only callers that go through it, and a cached binary or an unmigrated caller escapes it.

## Markdown tables

- Escape every `|` in a table cell, even inside backticks (`` head \| wc ``): GitHub splits the row before it parses code spans.
- Check a table in GitHub's rendered view, for the same reason.
- Compare each row's field count with the header's, for the same reason: `sed 's/\\|//g' FILE | awk -F'|' '{print NR": "NF}'`.
