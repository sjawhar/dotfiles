---
name: deepen
description: Run compound-engineering's plan-deepening pass against an existing plan document, standalone — scored triage picks the weak sections, targeted specialist reviewers strengthen them, one pass, no artifact conversion.
disable-model-invocation: true
---

# Deepen a Plan

Read `~/.dotfiles/vendor/compound-engineering/skills/ce-plan/references/deepening-workflow.md`
and run ONLY that deepening pass against the plan document the user names. The document stays
in its own format: no CE artifact conversion, no readiness frontmatter, no other ce-plan phase.
One pass, then report which sections scored, which were deepened, and which findings were
folded in versus dropped. Findings that would expand the plan's scope are called out for the
user, never silently added.
