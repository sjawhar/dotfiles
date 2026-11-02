---
name: design-review
description: Use when asked to review a plan or design before implementation, or when a plan needs architecture, security, UX, performance and bug review.
---

# Design review

1. **Find the plan:** the file or text the request names, else the latest plan in this conversation. If you find none, ask the user what to review.
2. **Trivial plan** (fewer than 50 lines and one file changed): suggest the `code-reviewer` agent instead of five agents. Continue only if the user agrees.
3. **Run five agents in parallel** in one `task` call. Give each the plan text and the existing code the plan affects, including code that calls or depends on it:
   - `code-architect`: architecture decisions, structural trade-offs, module decomposition, extensibility
   - `cybersecurity-expert`: security vulnerabilities, authentication and authorization, data exposure
   - `ux-designer`: user experience, API ergonomics, developer experience
   - `performance-engineer`: performance, scalability bottlenecks, optimization opportunities
   - `bug-finder`: subtle bugs, edge cases, failure modes, bad interactions with the rest of the codebase
4. **When all five finish, present one summary:**
   - Sort the findings: **Critical** (causes a bug, security hole or data loss; fix before implementing), **High** (works, but forces a rework later), **Medium** (an improvement with no known failure behind it), **Low** (style or preference).
   - Name where the agents agree, and where they disagree, with your recommendation.
