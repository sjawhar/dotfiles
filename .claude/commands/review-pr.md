---
description: "Comprehensive PR review using specialized agents"
argument-hint: "[review-aspects]"
allowed-tools: ["Bash", "Glob", "Grep", "Read", "Task"]
---

# Comprehensive PR Review

Run a comprehensive review of recent changes using multiple specialized agents, each focusing on a different aspect of code quality.

**Review Aspects (optional):** "$ARGUMENTS"

## Review Workflow:

1. **Determine Review Scope**
   - Check `jj status` and `jj diff` to identify changed files
   - Parse arguments to see if user requested specific review aspects
   - Default: Run all applicable reviews

2. **Available Review Aspects:**

   - **comments** - Analyze code comment accuracy and maintainability
   - **tests** - Review test coverage quality and completeness
   - **bugs** - Find subtle bugs, edge cases, and potential failure modes
   - **types** - Analyze type design and safety
   - **code** - General code review for project guidelines
   - **simplify** - Suggest simplifications for clarity and maintainability
   - **all** - Run all applicable reviews (default)

3. **Identify Changed Files**
   - Run `jj diff --name-only` to see modified files
   - Run `jj log -r @` to see current change description
   - Identify file types and what reviews apply

4. **Determine Applicable Reviews**

   Based on changes:
   - **Always applicable**: code-reviewer (general quality)
   - **If test files changed**: test-analyzer
   - **If comments/docs added**: comment-analyzer
   - **If types added/modified**: type-checker
   - **After other reviews pass**: code-simplifier (polish)
   - **For edge cases/bugs**: bug-finder

   **IMPORTANT: All agents run in READ-ONLY mode for this review.**
   When spawning agents that have edit capabilities (bug-finder, type-checker, code-simplifier),
   include this instruction in the prompt: "Analyze and report issues only. Do not edit files directly.
   Provide findings with file locations and suggested fixes, but do not make changes."

5. **Launch Review Agents**

   **Sequential approach** (one at a time):
   - Easier to understand and act on
   - Each report is complete before next
   - Good for interactive review

   **Parallel approach** (user can request):
   - Launch all agents simultaneously
   - Faster for comprehensive review
   - Results come back together

6. **Aggregate Results**

   After agents complete, summarize:
   - **Critical Issues** (must fix before merge)
   - **Important Issues** (should fix)
   - **Suggestions** (nice to have)
   - **Positive Observations** (what's good)

7. **Provide Action Plan**

   Organize findings:
   ```markdown
   # PR Review Summary

   ## Critical Issues (X found)
   - [agent-name]: Issue description [file:line]

   ## Important Issues (X found)
   - [agent-name]: Issue description [file:line]

   ## Suggestions (X found)
   - [agent-name]: Suggestion [file:line]

   ## Strengths
   - What's well-done in this PR

   ## Recommended Action
   1. Fix critical issues first
   2. Address important issues
   3. Consider suggestions
   4. Re-run review after fixes
   ```

## Usage Examples:

**Full review (default):**
```
/review-pr
```

**Specific aspects:**
```
/review-pr tests bugs
# Reviews only test coverage and bug detection

/review-pr comments
# Reviews only code comments

/review-pr simplify
# Simplifies code after passing review

```

**Parallel review:**
```
/review-pr all parallel
# Launches all agents in parallel
```

## Agent Descriptions:

**comment-analyzer**:
- Verifies comment accuracy vs code
- Identifies comment rot
- Checks documentation completeness

**test-analyzer**:
- Reviews behavioral test coverage
- Identifies critical gaps
- Evaluates test quality

**bug-finder**:
- Finds subtle bugs and edge cases
- Adversarial input analysis
- State and timing issues

**type-checker**:
- Analyzes type encapsulation
- Reviews invariant expression
- Rates type design quality
- Checks type safety implementation

**code-reviewer**:
- Checks project guideline compliance
- Detects bugs and issues
- Reviews general code quality

**code-simplifier**:
- Simplifies complex code
- Improves clarity and readability
- Applies project standards
- Preserves functionality

## Tips:

- **Run early**: Before creating PR, not after
- **Focus on changes**: Agents analyze jj diff by default
- **Address critical first**: Fix high-priority issues before lower priority
- **Re-run after fixes**: Verify issues are resolved
- **Use specific reviews**: Target specific aspects when you know the concern

## Version Control Note:

This workflow uses jj (Jujutsu) instead of git:
- `jj diff` instead of `git diff`
- `jj status` instead of `git status`
- `jj log -r @` to see current change

GitHub CLI (`gh`) commands still work for PR operations since jj pushes to git remotes.
