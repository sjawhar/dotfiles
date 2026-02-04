---
description: "Find a Claude Code session by topic, change ID, bookmark, or keywords"
argument-hint: [search-terms] [--after DATE] [--project DIR]
---

Find Claude Code session(s) matching: **$ARGUMENTS**

**If no search terms provided**, list the 5 most recent sessions.

**Search location:** `~/.dotfiles/.claude/projects/*/`

## Search Algorithm

**Step 1: Parse arguments**
- Extract `--after DATE` or `--project DIR` flags if present
- Everything else is the **search phrase** (keep as literal text, don't interpret as project names)

**Step 2: Find matching files**

Search strategy (flexible):
1. Try **exact phrase** first: `grep -l -i "thread inversion" ...`
2. If no results, try **all words (AND)**: files containing every word
3. Rank by match count

```bash
# Search ALL projects (don't filter by assumed project names)
grep -l -i "SEARCH_PHRASE" ~/.dotfiles/.claude/projects/*/*.jsonl 2>/dev/null
```

**Critical rules:**
- Do NOT assume words in the search are project names to filter by
- Only use `--project` flag for explicit project filtering

**Step 3: For each matching file, extract:**
- Session ID (filename without extension)
- Project (parent directory name)
- Date (file mtime)
- Topic (first non-system user message, truncated to 80 chars)
- Match count (`grep -c`)

**Step 4: Output**
- Sort by: match count (desc), then date (desc)
- Limit to top 10 results
- Format:
```
Session: <session-id>
Project: <project-name>
Date: <timestamp>
Topic: "<first user message snippet>..."
Matches: <count>
Resume: claude -r <session-id>
```

If no matches: "No sessions found matching '<terms>'. Try broader search terms."

## Common Search Patterns
- jj change IDs: "ttyomtpu"
- jj bookmarks: "fix-cli-explicit-targets"
- Issue references: "Issue #62"
- Topics: "thread inversion", "fingerprint"

## Filters (only when explicitly requested)
- `--after DATE`: filter by mtime
- `--project DIR`: limit to specific project directory
