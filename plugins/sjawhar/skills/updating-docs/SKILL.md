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
