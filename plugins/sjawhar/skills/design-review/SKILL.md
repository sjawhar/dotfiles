---
description: "Review a plan from architecture, security, UX, performance, and bug perspectives"
---

Review the current plan using five specialized agents **in parallel**.

**1. Locate the plan:**

- Check for an active plan file in `.claude/plans/`
- Or use the most recent plan discussed in this conversation
- If no plan is found, ask the user what to review

**2. For trivial plans** (< 50 lines, single-file change):

- Suggest using the `code-review` agent instead — five agents may be overkill
- Proceed only if user confirms

**3. Launch agents** (all in parallel):

1. **code-architect** - Evaluate architectural decisions, structural trade-offs, module decomposition, and extensibility
2. **cybersecurity-expert** - Identify security vulnerabilities, authentication/authorization issues, and data exposure risks
3. **ux-designer** - Review user experience implications, API ergonomics, and developer experience concerns
4. **performance-engineer** - Analyze performance implications, scalability bottlenecks, and optimization opportunities
5. **bug-finder** - Identify subtle bugs, edge cases, potential failure modes, and problematic interactions with the rest of the codebase

Provide each agent with:

- The plan content
- Context about relevant existing code that will be affected

**4. After all five complete, provide a unified design review summary:**

Categorize findings:

- **Critical:** Must address before implementation — blocking issues
- **High:** Should consider — important concerns
- **Medium:** Could improve — suggestions
- **Low:** Nice to have — minor enhancements

Also note:

- Areas where agents agree
- Areas where agents disagree (and your recommendation)

**Done when:** Unified summary is presented with all perspectives synthesized.
