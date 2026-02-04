---
description: "Run code quality agents on recent changes"
---

Analyze the code in my current working copy changes.

**1. Identify what to analyze:**
- Run `jj diff` to see modified files
- Run `jj diff --name-only` to get the list of changed files
- If there are no changes, report that and ask what to analyze

**2. Run agents in parallel (READ-WRITE mode):**

When spawning each agent, include this instruction: "Find issues and fix them directly. Edit files to implement your suggested improvements."

**Always run:**
1. **type-checker** - Improve type safety, replace Any types, add proper annotations
2. **bug-finder** - Find subtle bugs, edge cases, and potential failure modes
3. **code-simplifier** - Check for unnecessary complexity and simplification opportunities
4. **code-reviewer** - Check project guideline compliance and general code quality

**Conditionally run:**
5. **test-analyzer** - Review test coverage and quality (only if test files are in the diff)

**3. After all agents complete, provide a unified summary:**

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
