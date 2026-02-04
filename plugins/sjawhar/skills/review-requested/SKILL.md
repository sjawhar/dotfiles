---
description: "Find open PRs where I'm requested as a reviewer (excludes drafts and already-approved)"
---

Find all open PRs across your GitHub repos where you are specifically requested as a reviewer (not via team), excluding drafts and PRs you've already approved.

> **jj workspace note:** You may be in a non-default jj workspace with no `.git` directory. If `gh` commands fail, set `GIT_DIR` to point to the default workspace.

---

## Steps

**1. Get your GitHub username:**
```bash
gh api user -q '.login'
```

**2. Search for PRs requesting your review:**
```bash
gh search prs --review-requested=@me --state=open --draft=false --json repository,number,title,author,createdAt,url
```

Note: `--draft=false` excludes draft PRs.

**3. For each PR, check your review status:**
```bash
gh pr view <number> --repo <repo> --json reviews,reviewRequests
```

**Filter OUT PRs where:**
- Your most recent review is "APPROVED" (you're done with this one)

**Keep PRs where:**
- You haven't reviewed yet (still in `reviewRequests`)
- Your most recent review is "CHANGES_REQUESTED" or "COMMENTED" (needs re-review)

**4. Display results:**

```markdown
## 📋 Your Review Queue

| # | Repo | PR | Title | Author | Age | Status |
|---|------|-----|-------|--------|-----|--------|
| 1 | org/repo | [#123](url) | Add user authentication | @author | 2d | ⏳ Pending |
| 2 | org/repo | [#456](url) | Fix pagination bug | @author | 5d | 🔄 Changes requested |

Total: X PRs awaiting your review
```

**Status meanings:**
- ⏳ **Pending** - You haven't reviewed yet
- 🔄 **Changes requested** - You requested changes, author may have updated
- 💬 **Commented** - You left comments but didn't approve/reject

**5. Offer next steps:**
- "Want to start reviewing one? Give me the number or URL."
- If user picks one, suggest: "Run `/centaur-review <url>` to start a collaborative review"

---

**Done when:** Review queue is displayed with actionable next steps.
