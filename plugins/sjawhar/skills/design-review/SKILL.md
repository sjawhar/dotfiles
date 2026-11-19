---
name: design-review
description: Use when someone wants one plan or design reviewed before it is built by separate architecture, security, UX, performance and bug reviewers at once, or names this skill. Not when you are its single reviewer.
---

# Design review

1. Find the plan: the file or text the request names, else the latest plan in this conversation. If you find none, ask the user what to review.
2. A trivial plan (fewer than 50 lines and one file changed): suggest the `code-reviewer` agent instead of five agents, and continue only if the user agrees.
3. Run `code-architect`, `cybersecurity-expert`, `ux-designer`, `performance-engineer` and `bug-finder` in parallel in one `task` call. Give each the plan text and the existing code the plan affects, including code that calls or depends on it.
4. When all five finish, present one summary:
   - Sort the findings: Critical (causes a bug, security hole or data loss; fix before implementing), High (works, but forces a rework later), Medium (an improvement with no known failure behind it), Low (style or preference).
   - Name where the agents agree, and where they disagree, with your recommendation.
