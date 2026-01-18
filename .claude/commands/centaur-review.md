---
description: "Collaborative code review: you narrate, Claude tracks, challenges your ideas, then we generate a friendly GitHub review together"
args: "<pr_url_or_number>"
---

Human-AI collaborative code review. You lead the review by narrating your thoughts as you read the code. Claude tracks your comments, runs background analysis, and when you're done, challenges your suggestions and helps craft a polished GitHub review.

> **jj workspace note:** You may be in a non-default jj workspace with no `.git` directory. If `gh` commands fail, set `GIT_DIR` to point to the default workspace: `GIT_DIR=/path/to/default/.git gh pr view ...`

---

## Phase 1: Setup

**1. Parse PR argument** (required):
- The user must provide a PR URL or number as an argument
- If missing, ask: "Which PR should we review? Please provide the URL or number."

**2. Fetch PR context:**
```bash
gh pr view <pr> --json number,title,body,url,headRefName,baseRefName,additions,deletions,changedFiles,files
gh pr diff <pr>
```

**Store the file list and diff in memory** - you'll use this constantly to understand what the user is referring to.

**3. Fetch existing reviews and comments:**
```bash
# Get repo info
REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner')

# Get all reviews
gh api repos/$REPO/pulls/<number>/reviews

# Get all review comments (line-level)
gh api repos/$REPO/pulls/<number>/comments

# Get conversation comments (top-level)
gh pr view <pr> --comments
```

**Summarize the review history:**

For re-reviews, this is critical context. Analyze and categorize:

- **🔴 Your unaddressed change requests** - Changes you requested that don't appear to be fixed yet
- **❓ Open questions for you** - Questions from author or other reviewers awaiting your response
- **💬 Unresolved threads** - Discussion threads not marked resolved
- **✅ Resolved threads** - What's been addressed since your last review
- **📝 Other reviewers' concerns** - Issues raised by others you should be aware of

**Detect if this is a re-review:**
- Check if you have any previous reviews on this PR
- If yes, note what changed since your last review (commits after your review timestamp)

**4. Extract linked issues from PR body:**
- GitHub issues: Look for patterns like `Fixes #123`, `Closes #456`, `Resolves #789`
- Linear issues: Look for `linear.app` URLs (e.g., `https://linear.app/team/issue/TEAM-123`)

**5. Fetch issue details:**
- For GitHub issues: `gh issue view <number> --json title,body,labels`
- For Linear issues: Use Linear MCP tools if available, otherwise WebFetch the URL

**6. Launch background analysis agents** (all in parallel, non-blocking):

Launch five Task agents with `run_in_background: true`. **Do not present these results until Phase 3.**

1. **Bug Finder** (subagent_type: bug-finder):
   ```
   Find bugs, edge cases, and potential failure modes in this PR:
   - Logic errors and incorrect assumptions
   - Edge cases that aren't handled
   - Error handling gaps
   - Race conditions or concurrency issues
   - Security vulnerabilities

   Focus on significant issues. If nothing notable, say "Nothing to add."

   PR diff: [include diff]
   ```

2. **Code Simplifier** (subagent_type: code-simplifier):
   ```
   Review this PR for unnecessary complexity:
   - Over-engineered solutions
   - Abstractions that aren't needed
   - Code that could be more elegant or direct
   - Opportunities to simplify without losing functionality

   Focus on significant issues, not style preferences. If nothing notable, say "Nothing to add."

   PR diff: [include diff]
   ```

3. **Architecture Reviewer** (subagent_type: code-architect):
   ```
   Review the structural/architectural implications of this PR:
   - Does it change how modules interact?
   - Does it introduce new patterns or deviate from existing ones?
   - Are there changes that affect multiple parts of the codebase?
   - Anything that deserves discussion before merging?

   If nothing notable, say "Nothing to add."

   PR diff: [include diff]
   ```

4. **Requirements Checker** (subagent_type: general-purpose):
   ```
   Compare this PR to the linked issue requirements. Identify:
   - Requirements that are implemented correctly
   - Requirements that are missing or incomplete
   - Things the PR does that weren't in the requirements (scope creep?)
   - Deviations that might need discussion

   If everything looks good, say "Nothing to add."

   PR diff: [include diff]
   Issue: [include issue body]
   ```

5. **CLAUDE.md Compliance** (subagent_type: general-purpose):
   ```
   Check if this PR follows the conventions in the repo's CLAUDE.md file:
   - Coding patterns and styles mentioned
   - Architectural guidelines
   - Testing requirements
   - Any other repo-specific rules

   First, read the CLAUDE.md file in the repo root (if it exists).
   Then check the PR against those guidelines.

   If compliant or no CLAUDE.md exists, say "Nothing to add."

   PR diff: [include diff]
   ```

**7. Display summary and enter review mode:**

Show the user:
- PR title and description
- Files changed (list them with additions/deletions)
- Linked issue summary
- **Review history summary** (if this is a re-review):
  - 🔴 Your unaddressed change requests
  - ❓ Open questions awaiting your response
  - 💬 Unresolved discussion threads
  - 📝 Other reviewers' concerns to be aware of
- "Background analysis running... I'll track your comments as you review. 🚀"

**Then prompt:** "Ready when you are - start narrating your thoughts as you read through the code."

---

## Phase 2: Interactive Review Session

Track the user's observations as they narrate. **Do not show background agent results unless explicitly asked.**

**Maintain a comment tracker** with this structure:
```
Comments:
1. [file:line_range] [SEVERITY] - comment text
2. [file:line_range] [SEVERITY] - comment text
...
```

### Identifying File/Line Locations

**Use the PR context you fetched.** When the user makes an observation:

1. **Check if they mentioned a file or line explicitly** - use it directly
2. **If they quoted code**, search the PR diff for that snippet
3. **If they mentioned a concept** (e.g., "the error handling", "the new endpoint"), check which files in the PR diff contain related code
4. **If still unclear, ask immediately.** Have a LOW bar for asking: "Which file is this about? I see changes in `api.py`, `models.py`, and `tests/test_api.py`"

**Never silently guess wrong.** It's much better to ask than to track a comment on the wrong file.

### Interaction Cadence

- **Brief acknowledgment** after each observation: "Got it - tracking on `api.py:45` as a suggestion 👍"
- **If the user is in flow** (rapid observations), batch: "Tracking all three! 🚀"
- Keep it brief - don't interrupt the user's flow with lengthy responses

### User Commands During Review

- Say a severity word to tag the last comment: "blocking", "important", "suggestion", "question", "nitpick"
- "show comments" or "what do you have" - display current comment list
- "show analysis" - check background agent results (only if explicitly requested)
- "drop last" or "nevermind" - remove the last tracked comment
- "ready" or "done reviewing" - move to Phase 3

### Severity Mapping

- BLOCKING - "blocking", "must fix", "can't merge"
- IMPORTANT - "important", "should fix", "concern"
- SUGGESTION - "suggestion", "idea", "could", "maybe"
- QUESTION - "question", "wondering", "curious", "why"
- NITPICK - "nitpick", "minor", "tiny", "small thing"

---

## Phase 3: Consolidation

When the user says "ready" or "done":

**1. Verify comment locations:**
- For each comment, confirm it's associated with a specific file and line range
- If any are unclear, ask now: "A few comments need locations - which file/lines for [comment]?"

**2. Launch Red Teamer:**

Use the **red-teamer** agent (subagent_type: red-teamer) to challenge the user's comments:

```
The user has reviewed this PR and made the following observations. Your job is to challenge these - push back on anything that might be wrong, questionable, or not actually a good suggestion.

For each comment, consider:
- Is this actually a problem, or is the code correct?
- Could the reviewer be misreading the code?
- Is there context in the PR that explains why it's done this way?
- Would this suggestion actually improve the code, or make it worse?
- Are there counterarguments the PR author might raise?

Be direct. If a comment is solid, say so briefly. If it's weak or wrong, explain why.

User's comments:
[list each comment with file/line and observation]

PR diff:
[include relevant portions of diff]
```

**3. Gather background agent results:**
- Check all five background agents using TaskOutput
- Filter out any that said "Nothing to add"

**4. Present consolidated findings:**

```markdown
## Your Comments

| # | Location | Severity | Comment | Red Team Notes |
|---|----------|----------|---------|----------------|
| 1 | file.py:42 | BLOCKING | ... | [any challenges] |
| 2 | file.py:55-60 | SUGGESTION | ... | [any challenges] |

## AI Findings Worth Considering

### Bug Finder
[findings or "Nothing notable"]

### Code Simplifier
[findings or "Nothing notable"]

### Architecture
[findings or "Nothing notable"]

### Requirements
[findings or "Nothing notable"]

### CLAUDE.md Compliance
[findings or "Nothing notable"]

---

## Items Needing Your Input

1. [Red teamer challenged comment #2 - do you want to keep it?]
2. [Bug finder found X - should we add this to the review?]
3. [Any other decisions needed]
```

**Then ask:** "Take a look and let me know your decisions on the items above."

---

## Phase 4: Refinement

After the user provides input:

**1. Finalize the comment list** based on their decisions

**2. Write up each line-level comment:**

For each comment, format as:

```markdown
**[Location: file.py:42-45]**

[SEVERITY_BADGE] **[SEVERITY_WORD]**

[Clear, direct observation]

[If suggesting a change, use GitHub suggestion syntax:]
\`\`\`suggestion
[suggested code]
\`\`\`

[Optional: brief rationale or question]
```

### Severity Badges
- 🛑 BLOCKING
- ⚠️ IMPORTANT
- 💡 SUGGESTION
- 🔍 QUESTION
- 😹 NITPICK

### Tone Guidelines

**Priorities (in order):**
1. **Clarity** - the author should understand exactly what you mean
2. **Actionability** - they should know what to do (or what question to answer)
3. **Friendliness** - don't be harsh, but don't sacrifice clarity for niceness

**Emojis:** Use liberally to lighten tone, especially on critical comments: 😂 😹 😅 🙈 🚀 💪 ⚡ 🤔 👀

**Good patterns:**
- "I think this might [problem] because [reason]. What do you think? 🤔"
- "Could we [suggestion]? I'm thinking [rationale]."
- "Question: why [thing]? I expected [other thing]."
- "Tiny thing: [observation] 😅"
- End critical comments with softeners: "Let me know if I'm missing context! 😅" or "100% feel free to ignore 🙈"

**Avoid:**
- "This must change" / "Please fix this" / "This is wrong" / "You should..."
- Unnecessarily harsh or commanding tone
- Burying the point in excessive softening (clarity first, then soften)

### Example Comments by Severity

**🛑 BLOCKING:**
```markdown
🛑 **Blocking**

I think this could throw an IndexError if `items` is empty - and based on `caller.py:30`, that's a realistic scenario.

\`\`\`suggestion
if not items:
    return None
return items[0]
\`\`\`

Let me know if I'm missing context! 😅
```

**💡 SUGGESTION:**
```markdown
💡 **Suggestion**

Would it make sense to extract this into a helper? Similar logic exists in `other_file.py:123` 👀

Just a thought for maintainability - not blocking!
```

**🔍 QUESTION:**
```markdown
🔍 **Question**

Why `dict.get()` here instead of direct access? Is this defensive against a missing key case? 🤔

If so, maybe worth a brief comment for future readers.
```

**😹 NITPICK:**
```markdown
😹 **Nitpick**

The variable name `x` is a bit mysterious - maybe `user_count`?

100% feel free to ignore 🙈
```

**3. Write the top-level summary:**

```markdown
## 🎯 Review Summary

[1-2 sentence overall assessment - be genuine and direct]

### 🚧 Key Discussion Points
- [List BLOCKING and IMPORTANT items briefly]

### 📝 Inline Comments
- 🛑 X blocking | ⚠️ X important | 💡 X suggestions | 🔍 X questions | 😹 X nitpicks
```

Skip "What's Great" section unless something is genuinely impressive.

**Display everything** - all line comments followed by the top-level summary.

---

## Phase 5: Post

**1. Ask for approval:**
"Here's the complete review. Ready to post to GitHub?"

**2. If approved, post via GitHub API:**

First get the commit SHA and repo info:
```bash
COMMIT_SHA=$(gh pr view <pr> --json headRefOid -q '.headRefOid')
REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner')
```

Post review with inline comments:
```bash
gh api repos/$REPO/pulls/<number>/reviews \
  --method POST \
  -f body="[top-level summary]" \
  -f event="COMMENT" \
  -f commit_id="$COMMIT_SHA" \
  --input comments.json  # Array of {path, line, body}
```

**3. Confirm success:**
- Show link to the posted review
- "Review posted! 🚀 [link]"

---

## Quick Reference

| User Says | Claude Does |
|-----------|-------------|
| [narrates observation] | Track comment, infer file/line from PR context (ask if unclear) |
| "blocking" / "nitpick" / etc. | Update severity of last comment |
| "show comments" | Display current comment list |
| "show analysis" | Show background agent findings (only when asked) |
| "drop last" | Remove last comment |
| "ready" / "done" | Move to Phase 3 (consolidation) |
| [after consolidation decisions] | Move to Phase 4 (write up comments) |
| "post it" / "looks good" | Move to Phase 5 (post to GitHub) |

---

## Key Principles

1. **Hold back AI findings until Phase 3** - let the user form their own opinions first
2. **Low bar for asking clarification** - better to ask "which file?" than guess wrong
3. **Red team challenges user's comments** - not the PR itself
4. **Clarity over niceness** - be friendly but don't bury the point
5. **Line comments before summary** - specifics first, then overview
6. **Explicit approval required** - never post without user confirmation

---

**Done when:** Review is posted to GitHub and link is shared with user.
