---
description: "Find a Claude Code session by topic, change ID, bookmark, or keywords"
argument-hint: [search-terms] [--after DATE] [--project DIR]
---

Find Claude Code session(s) matching: **$ARGUMENTS**

**If no search terms provided**, list the 5 most recent sessions.

**Search location:** `~/.dotfiles/.claude/projects/*/`

**Search approach:**
1. Find `.jsonl` session files in the projects directory
2. Use `grep` to find sessions containing the search terms
3. For each match, extract:
   - Session ID (filename without extension)
   - Project directory
   - Start timestamp (from file or first entry)
   - First user message (to understand what the session was about)
   - Match count (more matches = more relevant)

**Common search patterns:**
- jj change IDs (e.g., "ttyomtpu", "xvyyvltz")
- jj bookmarks (e.g., "fix-cli-explicit-targets", "typed-deps-outs")
- Issue references (e.g., "Issue #62", "#77")
- Topics (e.g., "fingerprint", "remote caching")

**Filters:**
- Timeframe: "yesterday", "last week", "after Jan 5" → filter by mtime
- Project: limit to that project's subdirectory

**Output:**
- Sort by: match count (desc), then recency (desc)
- **Limit to top 10 results**
- If no matches: "No sessions found matching '<terms>'. Try broader search terms."

Format each result:
```
Session: <session-id>
Project: <project-name>
Date: <timestamp>
Topic: "<first user message snippet>..."
Matches: <count>
Resume: claude -r <session-id>
```

**Done when:** Results are presented (or "no matches" reported).
