# Fill Toggl Time Entries

Auto-fill missing Toggl time entries from desktop activity, Claude Code sessions, and existing entry patterns.

## Quick Reference

1. Collect data (existing entries, desktop activity, Claude sessions)
2. Analyze sessions via subagents
3. Detect time gaps
4. Classify gaps using session content → patterns → desktop hints
5. Present plan and get user approval
6. Create approved entries

**Key principle**: Claude session content is the primary source of truth for what was worked on; desktop activity only provides timing hints.

## Arguments

**Required** (first argument): Date or period
- `today`, `yesterday`
- Specific date: `2026-01-15`
- Date range: `2026-01-13..2026-01-15`

**Optional flags** (named parameters only):
- `--sessions <path>`: Path to directory containing Claude Code session JSONL files
- `--toggl-db <path>`: Path to local Toggl SQLite database

Examples:
- `/fill-toggl today`
- `/fill-toggl yesterday --sessions ~/claude-sessions`
- `/fill-toggl 2026-01-15 --sessions ~/transcripts --toggl-db ~/toggl.sqlite`

If a bare path is provided without a flag, ask the user to clarify whether it's a sessions path or Toggl DB path.

## Workflow

### Phase 1: Data Collection

1. **Determine date range** from user argument (default: today)

2. **Fetch existing Toggl entries** for the date range using `toggl_get_time_entries`

3. **Fetch previous week's entries** using `toggl_get_time_entries` with appropriate date range - this provides patterns for common descriptions, projects, and tags

4. **Get desktop activity** from Toggl:
   - If user provided a Toggl DB path, use it directly
   - Otherwise, ask the user where to find it (or if they want to skip local DB)
   - If local DB not available, use `toggl_get_timeline` API (note: rate limited to 30 req/hr)
   - If neither available, proceed with Claude sessions only

5. **Collect Claude Code sessions** if path provided:
   - Look for `.jsonl` files in the provided path
   - Each file is a session transcript

### Phase 2: Session Analysis

For each Claude Code session file found, **launch a subagent** (using Task tool with `subagent_type: "general-purpose"`) to analyze the session.

**Subagent prompt template:**
```
Analyze this Claude Code session transcript and extract activity blocks.

Session file: {path}

## Instructions
Read the JSONL file. Each line is a JSON object with:
- `type`: "user", "assistant", "summary", etc.
- `message`: The content
- `timestamp`: ISO timestamp
- `cwd`: Current working directory

## Your task
1. Identify time ranges of active work from timestamps
2. Summarize WHAT was being worked on (not just "coding" but specific tasks like "implementing OAuth flow")
3. Split into separate blocks when timestamp gaps > 30 minutes occur
4. Infer project names from directory paths, file names, or conversation content

## Output format (JSON only, no markdown fences)
{
  "session_file": "{filename}",
  "total_duration_minutes": 75,
  "blocks": [
    {
      "start": "2026-01-15T09:15:00Z",
      "end": "2026-01-15T10:30:00Z",
      "description": "Implementing OAuth2 flow for GitHub login",
      "inferred_project": "vivaria",
      "confidence": "high",
      "evidence": "Multiple files in /vivaria/auth/ were edited"
    }
  ]
}

If no activity found: {"session_file": "{filename}", "blocks": []}
If file is unreadable: {"session_file": "{filename}", "error": "description of issue"}
```

**Large session files:** If a session file exceeds 50,000 lines, sample: read first 1000 lines, last 1000 lines, and sample every 100th line in between. Focus on timestamps and conversation flow.

Run subagents in parallel for efficiency. Collect their outputs.

**Subagent error handling:**
- If a subagent fails to parse a file, log the error and continue with remaining files
- If a session file has no extractable activity blocks, exclude it silently
- If all subagents fail, proceed with desktop activity and pattern matching only

### Phase 3: Gap Detection

6. **Build a coverage map** from existing Toggl entries (list of covered time ranges)

7. **Find gaps** using this logic:
   - A gap is any time period where:
     a) No existing Toggl entry covers it, AND
     b) Either desktop activity (non-idle) exists, OR a Claude session was active
   - Merge adjacent activity into continuous gaps (don't report many tiny gaps)
   - Idle periods > 10 minutes within activity should split into separate gaps

8. **Filter out** gaps shorter than 15 minutes of actual activity

### Phase 4: Activity Classification

For each gap, determine what to fill it with:

**Primary source: Claude Code session content**
- If a Claude session covers this gap, use the session's description
- Don't rely on app names - "Terminal" could be anything
- The conversation content tells you what was actually happening

**Secondary source: Previous week patterns**
- Look for similar activities in recent entries
- Match by time of day, day of week, surrounding entries
- Reuse common descriptions, projects, tags

**Pattern matching heuristics:**
- Time-of-day matching: Activities at similar times on weekdays often repeat (standups, daily reviews)
- Surrounding context: If entries before/after match a pattern, the gap likely follows
- Description keywords: Match terms like "PR", "review", "meeting" from desktop activity to similar recent entries
- Mark pattern matches as lower confidence than Claude session matches

**Tertiary source: Desktop activity**
- Window titles can provide hints
- But don't over-categorize based on app names alone
- "Chrome" doesn't mean "browsing" - could be documentation, PRs, etc.

### Phase 5: User Confirmation

**⏸️ STOP AND WAIT FOR USER INPUT**

9. **Present the gaps** in a table:

| Time | Duration | Description | Project | Source |
|------|----------|-------------|---------|--------|
| 09:15-10:30 | 1h 15m | Code review for auth PR | vivaria | Claude session |
| 14:00-14:45 | 45m | Similar to "standup prep" | meetings | Pattern match |
| 16:30-17:15 | 45m | Development work | (unknown) | Desktop activity |

10. **Ask**: "What do you think?"

Wait for user feedback. They may:
- Approve all
- Modify some descriptions/projects
- Skip certain gaps
- Ask for more detail on sources

### Phase 6: Entry Creation

**Project and tag resolution:**
- Before creating entries, fetch available projects with `toggl_list_projects` and tags with `toggl_list_tags`
- Match inferred project names to actual project IDs (case-insensitive, partial match acceptable)
- If no project match found, leave project_id empty (entry will be unassigned)
- If user specifies a project name not found, ask whether to skip or use a different project

11. **Create entries** using `toggl_create_time_entry` for each approved gap

12. **Handle overlaps silently**:
    - If a proposed entry overlaps an existing one, trim the proposed entry's boundaries to avoid overlap
    - If the overlap is in the middle, split into two entries (before and after)
    - If trimming/splitting results in a segment shorter than 10 minutes, drop that segment
    - Never modify existing entries—only adjust proposed ones

13. **Report results**:
    - Number of entries created
    - Total time filled
    - Any entries that couldn't be created (and why)

## Local Toggl Database Details

If accessing the local SQLite database (user must provide the path):

**Database format**: SQLite 3.x with WAL, Core Data managed

**Key table**: `ZMANAGEDACTIVITY`
- `ZSTART`, `ZEND`: Cocoa timestamps (seconds since 2001-01-01 00:00:00 UTC)
- `ZFILENAME`: App name (e.g., "iTerm2", "Chrome")
- `ZTITLE`: Window title (e.g., "ssh", "✳ Claude Code")
- `ZISIDLE`: 0 = active, 1 = idle

**Timestamp conversion**: `cocoa_timestamp + 978307200 = unix_timestamp`

**Query example** (substitute the actual date):
```sql
SELECT
  datetime(ZSTART + 978307200, 'unixepoch', 'localtime') as start_time,
  datetime(ZEND + 978307200, 'unixepoch', 'localtime') as end_time,
  ZFILENAME as app,
  ZTITLE as title,
  ZISIDLE as is_idle
FROM ZMANAGEDACTIVITY
WHERE date(ZSTART + 978307200, 'unixepoch', 'localtime') = ?
  AND ZISIDLE = 0
ORDER BY ZSTART
```

## Notes

- The skill uses existing MCP tools: `toggl_get_time_entries`, `toggl_get_timeline`, `toggl_create_time_entry`, `toggl_list_projects`, `toggl_list_tags`
- Subagents are used for session parsing to avoid context pollution from long transcripts
- Calendar events appear as time entries - they're already in the coverage map
- No dry-run mode - just show the plan and ask for confirmation
- Use the default Toggl workspace (the user's primary workspace)
- All times should be displayed in the user's local timezone
- The Toggl API returns times in UTC—convert to local for display and comparison
