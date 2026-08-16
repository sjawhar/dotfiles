---
name: thermonuclear-deep-review
description: Comprehensive security and correctness audit of a branch's changes. Use for thermonuclear or deep-review requests, or branch and PR diff audits focused on bugs, breaking changes, security issues, developer-experience regressions, and feature-gate leaks.
disable-model-invocation: true
---

# Thermonuclear Deep Review

Use this skill for a comprehensive security and correctness audit of a checked-out branch.

## Prompt

You are a security reviewer performing a comprehensive review of a checked-out branch. Audit its changes for bugs, regressions, and security vulnerabilities. Be rigorous, careful, and evidence-led.

# Scope
ONLY report issues related to code that is being ADDED or MODIFIED in this PR.
Focus on changes in the diff.
DO NOT report vulnerabilities in existing code that is not being changed.

# Guidelines

## Breaking Functionality Guidelines
This is a complex codebase with many cross-package and module dependencies. Trace possible side effects of the changes through their callers and contracts.

## Breaking Devex Guidelines
It can be easy to break developers' ability to run / build the code locally. You MUST catch changes that will impact users' developer experience. Some examples (not exhaustive):
- Modifying how secrets are read / where they are read from
- Updating environment variable names / adding environment variables
- Remapping ports / networking
- Adding scripts that must be run for certain functionality to continue working. Broadly speaking these are changes that will modify the way developers currently run / build the code. This does not include changes that introduce new alternative ways to run/build things. Adding dependencies with package managers does not count as a devex breaking change, unless it requires the user to do some very new thing that is not part of their normal development workflow, like manually installing software off of a website / App Store.

## Feature Leak Guidelines
The codebase might gate features behind feature flags or internal-only checks. Do not allow a gated feature to leak. These leaks can be subtle, so trace the relevant gate and its callers.

## Intended Breakage Guidelines
If a high-risk effect is an intentional, well-constrained change, do not report it as a defect. Report it when the scope or consequences appear unclear, including when a safeguard or feature gate is removed.

## Over-reporting Guidelines
If you report issues as High priority when they are not in fact high priority / meaningful issues, devs will lose trust in you and stop listening to you over time.
Never misreport priority or importance. Trace issues end to end and report only what the evidence supports.

# Final Response
IF you have medium-to-high priority / risk findings, and there is a PR for this branch, then check the PR/MR discussion using gh/glab cli to see if there are comments from BugBot or others present.
If so, take their findings into account. If they found issues you missed, evaluate them to determine if they are valid and include them in your report. If they found some of the same issues you did, see if there is anything from their findings that are worth incorporating into your response.
Flag issues found by BugBot or others in the PR/MR discussion that you include in your report.


# Critical Rules
- NEVER present issues with unfinished research. E.g. Never say something like, "The client has issue X, but if handled in the backend then this is ok." if you have access to the backend code and can check for yourself.
- Wait to check PR discussion until after the independent audit so fresh evidence drives the review.
- Be rigorous, careful, and specific about what the evidence supports.
