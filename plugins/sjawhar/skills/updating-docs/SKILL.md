---
name: updating-docs
description: "Use when creating or updating documentation, READMEs, AGENTS.md files, skills, runbooks, code comments, or any prose that describes how a system works. Also use whenever a change touches documented behavior, even if the user asked only for the code change."
---

# Updating Docs

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

## Write what is true now

- Describe current behavior, in code comments too: readers act on the doc. Git log is the changelog.
- Delete on sight: PR or issue breadcrumbs ("as of #1234"), migration trails ("previously X, now Y"), "NEW:" or "UPDATED:" markers, dated notes.
- When the old way is gone, delete the warnings against it and describe the correct way; a warning keeps the dead option alive.

## Write a rule

- State what the rule requires and what it does not cover, or agents apply it past its edge.
- Keep provenance (who, when, the incident) in the commit message or PR body. The rule text gets at most a one-line citation (a link, `file:line`), never a dated story, tally or justifying quote: agents read the story as the rule.
- Delete a committed policy (gating file, threshold, process doc) when no commit message or PR body records why Sami asked for it, and when.
- Write "always" or "never" only for a rule with no exception. State the exception, or an agent that meets it drops the whole rule.
- Before you add a sentence, correct the existing one that is wrong, incomplete or ambiguous. Delete or narrow the sentence that caused the failure; a correction appended beside it leaves both readings.

## Correct an overturned belief

- Correct every copy in place (headings, titles, flags, acceptance rows, downstream steps): a correction under a heading that still asserts the old model has not landed.
- Grep misses a copy that shares no words with the claim, so also list and check each file, flag, test or step that assumed the old state.
- Apply the corrected rule to the case that prompted it; don't only restate it.
- Record what changed in the commit message, not in a note beside the corrected text, which readers take as current.
- Mark each claim you didn't observe as inferred: a correction's overreach spreads faster than the original error.
- When a decision says what code may accept, make the code refuse the rest in the same change; a spec alone changes nothing it accepts.

## On a cutover, sweep the changed directory

- List files with a tree walk (`git ls-files <dir>`), never a hand-written glob, which misses nested paths. Check every file type: scripts and configs record what runs.
- Before you sweep, write down the questions each file must answer (stale claim? resolved?); without them you check only what you happen to notice.
- When the claim matters, have a second agent (`task` with `scout`) sweep from the claim itself, not your search terms, and merge the hits: one searcher's grep misses in its own way.

## Read the whole artifact back

- After an edit, read the whole document, not a grep for what you added: check that every part of the edit landed where you meant it, since an insert can take the wrong anchor, and that untouched neighbors survived.
- Before you trust a read-back, make it find a string you know is there, so a broken reader can't report success.
- A change to a string invalidates offsets computed on it. Apply `(start, end, text)` edits back to front, or replace the region in one slice; check the result.
- Chain a publish command to its guard with `&&`, not on the next line, or start the script with `set -euo pipefail`: a newline is not a guard.

## Claims in prose

- Measured X only on A? Write "A: X; B: not checked", not "A and B: X".
- Paraphrase a result from the run's own summary line, with every count it prints; a claim about one test needs that test's own record.
- Before you describe a format, print one instance verbatim.
- Prose owns only what its own diff makes true. When another PR, branch or system it describes changes, correct the text in place.
- Sweep docstrings of tests and witnesses (code that demonstrates a claim) too: each states only what its assertion checks.

## Integrate, don't accrete

- Before you add a section, look for one on the topic. If it mostly covers it, rewrite it; patched-in sentences are how two sections come to disagree.
- Merge per-case sections (per provider, per version) that repeat each other into one that names the differences.
- A consolidation leaves the doc shorter. One doc per audience or purpose: merge duplicate files, and state in the PR body why no existing file can hold a new one's content.
- A doc that grows on every edit, even simplifying ones, is accreting: rewrite it.

## A rule agents know but keep forgetting

- Stop rewording it; build a refusal or an automatic step at the point of action: rewording already failed.
- A mechanism binds only callers that go through it. Measure who it reaches: a cached binary or an unmigrated caller escapes it.

## Markdown tables

Before you edit one, read `references/markdown-tables.md`: GitHub splits rows on any unescaped `|`.
