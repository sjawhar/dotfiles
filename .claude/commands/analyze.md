---
description: "Run type-checker, bug-finder, and code-simplifier on recent changes"
---

Analyze the code in my current working copy changes.

**1. Identify what to analyze:**
- Run `jj diff` to see modified files
- If there are no changes, report that and ask what to analyze

**2. Run these three agents in parallel:**
1. **type-checker** - Improve type safety, replace Any types, add proper annotations
2. **bug-finder** - Find subtle bugs, edge cases, and potential failure modes
3. **code-simplifier** - Check for unnecessary complexity and simplification opportunities

**3. After all three complete, provide a unified summary:**

Categorize findings by severity:
- **Critical:** Must fix before shipping — bugs, security issues, type errors that cause runtime failures
- **High:** Should fix — subtle bugs, missing edge cases, unsafe type casts
- **Medium:** Consider fixing — type improvements, simplifications that improve clarity
- **Low:** Optional — style improvements, minor suggestions

For each issue, include:
- File and line number
- Brief description of the issue
- Suggested fix (with code example where helpful)

**Done when:** Summary is presented with all findings categorized by severity.
