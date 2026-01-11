---
description: "Review a plan from architecture, security, UX, performance, and bug perspectives"
---

Review the current plan using five specialized agents **in parallel**:

1. **code-architect** - Evaluate architectural decisions, structural trade-offs, module decomposition, and extensibility
2. **cybersecurity-expert** - Identify security vulnerabilities, authentication/authorization issues, and data exposure risks
3. **ux-designer** - Review user experience implications, API ergonomics, and developer experience concerns
4. **performance-engineer** - Analyze performance implications, scalability bottlenecks, and optimization opportunities
5. **bug-finder** - Identify subtle bugs, edge cases, potential failure modes, and problematic interactions with the rest of the codebase

Read the current plan file first, then launch all five agents simultaneously with the plan content.

After all five complete, provide a unified design review summary:
- **Blocking issues** that must be addressed before implementation
- **Important concerns** that should be considered
- **Suggestions** for improvements
- Areas where the agents agree or disagree

Format the output with clear sections for each perspective, then the synthesis.
