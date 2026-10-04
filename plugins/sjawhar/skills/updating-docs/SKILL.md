---
name: updating-docs
description: "Use when creating or updating documentation, READMEs, AGENTS.md files, skills, runbooks, or any prose that describes how a system works. Trigger whenever a change touches documented behavior, even if the user only asked for the code change."
---

# Updating Docs

## Evergreen, not archaeological

Write what is true now. Remove on sight:

- PR/issue-number breadcrumbs ("as of #1234...")
- Migration trails ("previously we did X, now we do Y")
- Deprecation lists ("don't use the old JSON format") once the old way is gone — describe the correct way instead
- "NEW:" / "UPDATED:" markers and dated notes

If a reader needs to know the old way existed, that's what `git log` is for.

## Writing a rule

A rule states the rule and its boundary — what it requires, and what it doesn't cover.

- **Provenance stays out of the rule.** Who decided it, when, and the incident that prompted it belong in the commit message or PR body, with at most a one-line citation in the rule text (a link, a `file:line`) — never a dated story, a lane count, or a quote used to justify the rule. A reader needs the rule, not the trial that produced it.
- **No always/never unless the rule truly has none.** Absolute phrasing invites an agent to apply it past its real edge; state the exception where one exists rather than writing an exceptionless rule and hoping nobody hits the edge.
- **Tighten before you add.** Before writing a new paragraph, find the existing sentence that's wrong, incomplete or ambiguous and fix that one. If a sentence caused the failure, the fix is usually to delete or narrow it, not to append a correction beside it.
- **When a ruling overturns a belief, find and correct every copy** — not just the sentence that stated it, but headings, flags, acceptance rows and downstream steps that assumed it, even where they share no words with the original claim (see the sweep rules below).

## A cutover sweeps the whole owning tree

Enumerate the owning tree by a tree walk (`git ls-files <dir>`, `rg --files <dir>`), never a hand-written glob — a glob misses nested paths a tree walk doesn't, and what it misses keeps teaching the old behavior. Cover every file type: scripts and configs in a skill tree are the doc of record for what actually runs, not just the `.md`.

- **One searcher's grep false-negatives in its own way** (a remembered phrase instead of the claim, an alternation that misses a spelling, a truncated listing). When the claim matters, have a second agent sweep independently from the claim itself, not from your fragment, and merge the hits.
- **A copy that shares no words with the retracted claim is invisible to any grep.** Enumerate what DEPENDED on the overturned state instead — a heading, a flag, a downstream step, an acceptance row — and check each one directly. Re-apply a stated rule to the case that prompted it, not just restate it; before characterizing a format, print one instance of it verbatim rather than deriving it from an aggregate.
- **A surface list fixes WHERE to look but silently also fixes WHAT you're checking.** A surface can carry more than one question (does it hold a stale claim? is it resolved?) — write down each surface's question before sweeping it.
- **A ruling about what code may accept lands as a refusal in the code, in the same change** — not only in a spec or comment. Otherwise later work re-derives the old answer from what the code still accepts.

## Read the whole artifact back after you edit it

Not a grep for what you added — that finds only what you already suspect. Read the document through once and confirm the neighbours survived: the clauses a reader needs that your edit never mentions.

- A read-back only counts if its reader can distinguish present from absent; before trusting one, make it find a string you know is there.
- A multi-paragraph edit (replace plus insert) can land its insert against the wrong anchor; verify both presence and position, not just presence.
- An index or offset computed on a string is invalidated by any mutation of that string. Apply edits as `(start, end, text)` triples back-to-front, or replace the whole region once in a single slice — then assert on the result, not on the inputs.
- Chain the command that publishes a guarded step's output to the guard itself (`&&`), not on the next line — a newline is not a guard; `set -euo pipefail` at the top of a script guards without needing the chain.
- A conjunctive claim ("A and B: X") inherits its evidence only from whichever member you actually measured; scope the claim to that member and say outright what you didn't check.
- Paraphrase a result from the run's own summary line, not from your conclusion — a paraphrase that drops a category is a selection, not a summary. A claim about one specific test needs the per-test record, not the aggregate line.
- Prose can only own what its own diff makes true. A claim about another PR, branch or system expires at that thing's next change; correct it in place when it moves, with what changed in the commit message, never in the corrected text.
- Sweep test and witness docstrings too — a docstring on a passing assertion inherits that assertion's authority without anything having checked the docstring itself. State in it only what the assertion checks.
- A correction that leaves the heading or title asserting the old model has not landed — the heading is what a skim reads. Correct it in place; say what changed in the commit message, never append a note below an uncorrected heading.
- When correcting an overturned claim, write what you observed and label separately what you infer — a correction inherits the authority of the thing it corrects, so its overreach spreads faster than the original error did.

## A rule broken by people who can quote it wants a mechanism

When agents who know a standing instruction keep violating it, and the honest answer to "why" is "they forgot," stop rewording it: build a refusal or an automatic step at the point of action instead, and measure who it reaches — a mechanism only binds the population that actually goes through it (a cached binary, an unmigrated caller).

## Integrate, don't accrete

Before adding a section, check whether an existing section already covers the topic. If it mostly does, rewrite that section — don't append a near-duplicate beside it. Symptoms you're accreting instead of integrating:

- The doc gets longer every edit, even when the change simplified things
- Two sections disagree slightly about the same behavior
- Per-case sections (per-provider, per-version) that share most of their content — merge into one section with the differences called out

Same test as code: an update whose purpose is consolidation should leave the doc shorter.

## Structure

- One doc per audience/purpose; consolidate rather than proliferate files. A new file needs a reason an existing one can't serve.
- Match the tone and formatting of the doc you're editing.
- Prefer rewriting a section over patching sentences into it — patched-in sentences are how docs drift into incoherence.
- A pipe inside a table cell needs escaping even inside backticks (`` head \| tr \| wc ``) — GitHub's table extension splits a row on every unescaped `|` before inline parsing, so a code span doesn't protect it, and a doc change must be reviewed as rendered, not only as source. Check with `sed 's/\\|//g' FILE | awk -F'|' '{print NR": "NF}'` against the header count.
