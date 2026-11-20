---
name: design-review
description: Use when someone wants one plan or design reviewed before it is built by separate architecture, security, UX, performance and bug reviewers at once, or names this skill. Not when you are its single reviewer.
---

# Design review

1. Find the plan: the file or text the request names, else the latest plan in this conversation. If you find none, ask the user what to review.
2. A plan is trivial when its text is fewer than 50 lines and it changes one file. For a trivial plan, suggest the `code-reviewer` agent instead of five agents, and ask the user which they want. Go on to step 3 only if the user chooses the five agents.
3. Run these five agents in parallel in one `task` call. Give each the plan text, the existing code the plan affects (including code that calls or depends on it), and what it reviews:
   - `code-architect`: architecture decisions, structural trade-offs, module decomposition, extensibility.
   - `cybersecurity-expert`: security vulnerabilities, authentication and authorization, data exposure.
   - `ux-designer`: user experience, API ergonomics, developer experience.
   - `performance-engineer`: performance, scalability bottlenecks, optimization opportunities.
   - `bug-finder`: bugs, edge cases, failure modes, bad interactions with the rest of the codebase.
4. When all five finish, present one summary:
   - Sort the findings: Critical (causes a bug, security hole or data loss; fix before implementing), High (works, but forces a rework later), Medium (an improvement with no known failure behind it), Low (style or preference).
   - Name where the agents agree, and where they disagree, with your recommendation.
