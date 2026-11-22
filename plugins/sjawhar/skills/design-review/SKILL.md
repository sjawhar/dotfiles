---
name: design-review
description: Use when someone wants one plan or design reviewed before it is built by separate architecture, security, UX, performance and bug reviewers at once, or names this skill. Not when you are its single reviewer.
---

# Design review

1. **Find the plan:** the file or text the request names, else the latest plan in this conversation.
   - If you find none, ask the user what to review.
2. **Check whether the plan is trivial.** A plan is trivial when its text is fewer than 50 lines and it changes one file.
   - For a trivial plan, suggest the `code-reviewer` agent instead of five agents.
   - For a trivial plan, ask the user which they want.
   - Go on to step 3 only if the user chooses the five agents.
3. **Dispatch these five reviewers:**

   | Agent | Reviews |
   | --- | --- |
   | `code-architect` | Architecture decisions, structural trade-offs, module decomposition, extensibility |
   | `cybersecurity-expert` | Security vulnerabilities, authentication and authorization, data exposure |
   | `ux-designer` | User experience, API ergonomics, developer experience |
   | `performance-engineer` | Performance, scalability bottlenecks, optimization opportunities |
   | `bug-finder` | Subtle bugs, edge cases, failure modes, bad interactions with the rest of the codebase |

   - Run these five agents in parallel in one `task` call.
   - Give each the plan text, the existing code the plan affects (including code that calls or depends on it), and what it reviews.
4. **Present one summary** when all five finish:
   - Sort the findings by level:

     | Level | Meaning | Action |
     | --- | --- | --- |
     | Critical | Causes a bug, security hole or data loss | Fix before implementing |
     | High | Works, but forces a rework later | |
     | Medium | An improvement with no known failure behind it | |
     | Low | Style or preference | |

   - Name where the agents agree.
   - Name where the agents disagree, with your recommendation.
