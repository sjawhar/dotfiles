---
description: "Find a Claude Code or OpenCode session by topic, change ID, bookmark, or keywords"
argument-hint: [search-terms] [--after DATE] [--project DIR] [--source claude|opencode|all]
---

Find session(s) matching: **$ARGUMENTS**

**If no search terms provided**, list the 5 most recent sessions from each source.

## Sources

Search **both** Claude Code and OpenCode sessions by default.

| Source | Storage | Method |
|--------|---------|--------|
| Claude Code | `~/.dotfiles/.claude/projects/*/*.jsonl` | `grep` on JSONL files |
| OpenCode | `~/.local/share/opencode/storage/` | Built-in `session_search` / `session_list` tools |

Use `--source claude` or `--source opencode` to limit to one source.

## Search Algorithm

**Step 1: Parse arguments**
- Extract `--after DATE`, `--project DIR`, or `--source` flags if present
- Everything else is the **search phrase** (keep as literal text, don't interpret as project names)

**Step 2a: Search Claude Code sessions**

Search strategy (flexible):
1. Try **exact phrase** first: `grep -l -i "thread inversion" ...`
2. If no results, try **all words (AND)**: files containing every word
3. Rank by match count

```bash
# Search ALL projects (don't filter by assumed project names)
grep -l -i "SEARCH_PHRASE" ~/.dotfiles/.claude/projects/*/*.jsonl 2>/dev/null
```

For each matching file, extract:
- Session ID (filename without extension)
- Project (parent directory name)
- Date (file mtime)
- Topic (first non-system user message, truncated to 80 chars)
- Match count (`grep -c`)

**Step 2b: Search OpenCode sessions (in parallel with 2a)**

Use built-in tools:
- **With search terms**: `session_search(query="SEARCH_PHRASE")` — searches across all OpenCode session messages
- **Without search terms**: `session_list(limit=5)` — lists recent sessions

For each result, use `session_info(session_id=...)` to get metadata (message count, date range, agents used).

**Critical rules:**
- Do NOT assume words in the search are project names to filter by
- Only use `--project` flag for explicit project filtering
- Run Claude Code grep and OpenCode session_search **in parallel**

**Step 3: Output**

Merge results from both sources. Sort by: match count (desc), then date (desc). Limit to top 10 per source.

Format:
```
[Claude Code] Session: <session-id>
Project: <project-name>
Date: <timestamp>
Topic: "<first user message snippet>..."
Matches: <count>
Resume: claude -r <session-id>

[OpenCode] Session: <session-id>
Date: <date-range>
Messages: <count>
Agents: <agent-list>
Resume: opencode -r <session-id>
```

If no matches: "No sessions found matching '<terms>'. Try broader search terms."

## Common Search Patterns
- jj change IDs: "ttyomtpu"
- jj bookmarks: "fix-cli-explicit-targets"
- Issue references: "Issue #62"
- Topics: "thread inversion", "fingerprint"

## Filters (only when explicitly requested)
- `--after DATE`: filter by mtime (Claude Code) or `from_date` param (OpenCode)
- `--project DIR`: limit to specific project directory
- `--source claude|opencode|all`: limit to one source (default: all)
