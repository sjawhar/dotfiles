---
name: updating-docs
description: "Use when creating or updating documentation, READMEs, AGENTS.md files, skills, runbooks, code comments, or any prose that describes how a system works. Also use whenever a change touches documented behavior, even if the user asked only for the code change."
---

# Updating Docs

## Write what is true now

- Describe current behavior, in code comments too. Git log is the changelog.
- Delete on sight: PR or issue breadcrumbs ("as of #1234"), migration trails ("previously X, now Y"), "NEW:" or "UPDATED:" markers, dated notes.
- When the old way is gone, delete warnings against it and describe the correct way.

## Write a rule

- State the rule and its boundary: what it requires and what it does not cover.
- Provenance (who, when, the incident) goes in the commit message or PR body. The rule text gets at most a one-line citation (a link, a `file:line`), never a dated story, a tally or a justifying quote.
- Delete a committed policy (a gating file, a threshold, a process doc) that has no source. Its source is what Sami said, and when, recorded in the commit message or PR body.
- Write "always" or "never" only for a rule with no exception. State the exception where one exists.
- Before you add a sentence, find the existing sentence that is wrong, incomplete or ambiguous and correct it. If a sentence caused the failure, usually delete or narrow it. Don't append a correction beside it.

## Correct an overturned belief

- Correct every copy in place: headings, titles, flags, acceptance rows, downstream steps. A correction under a heading that still asserts the old model has not landed.
- Find every copy with the steps in "Sweep the whole owning tree" below. Grep can't find a copy that shares no words with the claim, so also list what depended on the old state and check each item.
- Re-apply the rule to the case that prompted it, not only restate it.
- Record what changed in the commit message, never in a note beside the corrected text.
- Label what you infer apart from what you observed. A correction's overreach spreads faster than the original error.
- Land a ruling about what code may accept as a refusal in the code, in the same change. A spec or comment alone is not enough.

## Sweep the whole owning tree on a cutover

- List files with a tree walk (`git ls-files <dir>`, `rg --files <dir>`), never a hand-written glob, which misses nested paths. Check every file type: scripts and configs in a skill tree are the record of what runs.
- Before you sweep a surface, write down each question it must answer (stale claim? resolved?).
- When the claim matters, have a second agent (`task` with `scout`) sweep from the claim itself, not from your search terms. Merge the hits.

## Read the whole artifact back

- After an edit, read the whole document, not a grep for what you added. Check that the neighbors your edit never touched survived.
- Before you trust a read-back, make it find a string you know is there.
- Check each part of a multi-part edit for presence and position.
- A change to a string invalidates offsets computed on it. Apply `(start, end, text)` edits back to front, or replace the region in one slice. Assert on the result.
- Chain a publish command to its guard with `&&`, not on the next line, or start the script with `set -euo pipefail`.

## Claims in prose

- Scope "A and B: X" to the member you measured. Say what you didn't check.
- Paraphrase a result from the run's own summary line, with every category. A claim about one test needs its own record.
- Before you describe a format, print one instance verbatim.
- Prose owns only what its own diff makes true. When another PR, branch or system it describes changes, correct the text in place.
- Sweep test and witness docstrings too. Each states only what its assertion checks.

## A rule agents know but keep forgetting

- Stop rewording it. Build a refusal or an automatic step at the point of action.
- A mechanism binds only callers that go through it. Measure who it reaches: a cached binary or an unmigrated caller escapes it.

## Integrate, don't accrete

- Before you add a section, look for one on the topic. If it mostly covers it, rewrite it. Rewrite sections rather than patch sentences in.
- Signs of accretion:
  - The doc grows on every edit, even edits that simplified.
  - Two sections disagree.
  - Per-case sections (per provider, per version) repeat each other. Merge them into one and call out the differences.
- A consolidation leaves the doc shorter.
- One doc per audience or purpose. Consolidate files rather than proliferate them. A new file needs a reason an existing one can't serve.
- Match the tone and format of the doc you edit.

## Markdown tables

- Escape every `|` in a table cell, even inside backticks (`` head \| wc ``). GitHub splits the row before it parses code spans.
- Review a doc change as rendered. Compare each row's field count to the header's: `sed 's/\\|//g' FILE | awk -F'|' '{print NR": "NF}'`.
