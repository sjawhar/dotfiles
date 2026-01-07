---
description: "Find a Claude Code session by topic, change ID, bookmark, or keywords"
argument-hint: [search-terms] [--after DATE] [--project DIR]
---

Find the Claude Code session(s) in `~/.dotfiles/.claude` matching the search criteria: **$ARGUMENTS**

Search approach:
1. Search through `~/.dotfiles/.claude/projects/*/` for `.jsonl` session files
2. Use `grep` to find sessions containing the search terms (jj change IDs, bookmarks, issue numbers, topics, keywords)
3. For each match, extract:
   - Session ID (filename)
   - Project directory
   - Start timestamp
   - First user message (to understand what the session was about)
   - How many times the search term appears (more = more relevant)

Common search patterns:
- jj change IDs (e.g., "ttyomtpu", "xvyyvltz")
- jj bookmarks (e.g., "fix-cli-explicit-targets", "typed-deps-outs")
- Issue references (e.g., "Issue #62", "#77")
- Topics (e.g., "fingerprint", "remote caching")

If a timeframe is specified (e.g., "yesterday", "last week", "after Jan 5"), filter by file modification time.

If a project directory is specified, limit search to that project's sessions.

Present results sorted by relevance (match count) and recency, showing:
- Session ID
- Project
- Date/time
- Brief description of what the session was working on

The user can then use `claude -r <session-id>` to resume a session.
