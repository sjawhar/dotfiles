---
name: updating-docs
description: "Use when creating or updating documentation, READMEs, AGENTS.md files, skills, runbooks, or any prose that describes how a system works. Covers rewriting docs after a code change, consolidating overlapping sections, and reviewing docs for staleness. Trigger whenever a change touches documented behavior, even if the user only asked for the code change."
---

# Updating Docs

Docs describe the current state of the system, as one cohesive story. They are not a changelog and not a narrative of how we got here — version control holds the history.

## Evergreen, not archaeological

Write what is true now. Remove on sight:

- PR/issue-number breadcrumbs ("as of #1234...")
- Migration trails ("previously we did X, now we do Y")
- Deprecation lists ("don't use the old JSON format") once the old way is gone — describe the correct way instead
- "NEW:" / "UPDATED:" markers and dated notes

If a reader needs to know the old way existed, that's what `jj log` is for.

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
