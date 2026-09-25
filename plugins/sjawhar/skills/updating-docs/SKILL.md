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

If a reader needs to know the old way existed, that's what `jj log` is for.

## A cutover sweeps the whole owning tree

When a change or ruling makes prose false, the sweep that fixes it enumerates every file in the owning tree by tree walk — `jj file list <dir>`, `rg --files <dir>` — never by a hand-written glob: `.claude/skills/*/references/*.md` cannot see `references/phases/`, and what the glob misses keeps teaching the old behavior. The sweep covers every file type, not `.md` alone: scripts and configs in a skill tree are the doc of record for what actually runs. On agent-c #19273 (2026-09-18, 35 files) the skill text said docker/hawk while `run_probe.sh:16` two directories down still hardcoded `--sandbox modal` — "the script is what actually runs, so the doc was lying." Inferred from that owner's sweep; not a sentence Sami wrote.

**A sweep for one retracted claim wants more than one searcher.** A single searcher's grep false-negatives in its own way: a remembered phrase instead of the claim, an alternation that misses a spelling, a `head -5`. On 2026-09-25 three independent sweeps for one stale claim (the owner's, the reviewer's, the fixer's) each missed sites the others found, the fixer's alone found two in a file neither of the others searched, and only their union was complete (Fix PR20044 lane). Separately, the merge queue's alternation grep and the dispatch lane's `head -5` each false-negatived the same real #20125 content. When the claim matters, have a second agent sweep independently, from the claim rather than your fragment, and merge the hits.

**A ruling about what code may accept lands as a refusal in the code, in the same change.** Recording a ruling is not landing it. When it decides which target, source or shape the code takes, the change that records it makes the code refuse the alternatives, with an error that names the ruling. Otherwise every later lane re-derives the old answer from what the code still accepts. On 2026-09-25 three lanes traced one case: the 09-13 ruling that assignment repositories are AWS CodeCommit lived in a spec and a code header, while the credential mint accepted any git URL. Lane after lane derived GitHub from the one credential the code could mint, and Sami was asked for GitHub repositories four times ('Why the fuck does this keep happening?'; task delivery, env typing, platform PO).

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
