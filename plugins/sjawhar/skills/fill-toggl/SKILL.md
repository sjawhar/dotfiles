---
name: fill-toggl
description: Use when auto-filling missing Toggl time entries from desktop activity, Claude Code sessions, and existing patterns
---

# Fill Toggl Time Entries

**REQUIRED SUB-SKILL:** Use `using-toggl` for Toggl MCP tools.

Auto-fill missing Toggl time entries from desktop activity, Claude Code sessions, and existing entry patterns.

## Quick Reference

1. Collect data (existing entries, desktop activity, Claude sessions)
2. **Clean up multi-tag entries** (every entry must have exactly one tag)
3. Analyze sessions via subagents
4. Detect time gaps (desktop activity required, not just Claude sessions)
5. Classify gaps using session content → patterns → desktop hints
6. Present plan and get user approval
7. Create approved entries
8. Verify and iterate until all completion criteria pass

**Key principle**: Claude session content is the primary source of truth for what was worked on; desktop activity provides both timing AND validation that you were actively working (not just background processes).

## Arguments

**Required** (first argument): Date or period
- `today`, `yesterday`
- Specific date: `2026-01-15`
- Date range: `2026-01-13..2026-01-15`

**Optional flags** (named parameters only):
- `--sessions <path>`: Path to directory containing Claude Code session JSONL files
- `--toggl-db <path>`: Path to local Toggl SQLite database

**Default data locations** (used if flags not provided):
- Toggl DB: `/data/toggl/production/DatabaseModel.sqlite`
- Claude sessions: `/data/claude/*/` (searches all home directories)

These paths are checked automatically. If not found, the command will ask.

**Timezone handling:**
- Dates are interpreted in the user's local timezone (inferred from system)
- To specify explicitly: `2026-01-18T00:00 America/Los_Angeles`
- All displayed times use local timezone
- When comparing with UTC data from APIs, convert appropriately

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
   - Otherwise, check default path `/data/toggl/production/DatabaseModel.sqlite`
   - If not found, ask the user where to find it (or if they want to skip local DB)
   - If local DB not available, use `toggl_get_timeline` API (note: rate limited to 30 req/hr)
   - If neither available, proceed with Claude sessions only

5. **Collect Claude Code sessions**:
   - If `--sessions` provided: use that path
   - Otherwise, scan `/data/claude/*/` for all home directories
   - For EACH home directory found:
     - Look in `.claude/projects/*/` for session `.jsonl` files
     - Include sessions that overlap with the target date range
   - Report: "Found X sessions across Y projects in Z home directories"

### Phase 1.5: Multi-Tag Entry Cleanup

**Rule**: Every time entry MUST have exactly ONE tag. This applies to ALL entries in the date range, not just newly created ones.

Scan ALL existing entries in the date range for tag violations:

1. **Find multi-tag entries**: Query entries where tag count > 1
2. **For each multi-tag entry**:
   - Present to user: "Entry X has tags [A, B, C]. How should we split?"
   - Cross-reference with Claude sessions for that time to propose intelligent splits
   - Propose splitting by activity type OR by equal duration as fallback
   - Get user approval
   - Delete original entry and create separate single-tag entries

3. **Find zero-tag entries**: Also flag entries with NO tags for user to assign one

Complete this cleanup before proceeding to gap detection.

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
     b) Desktop activity (non-idle) exists
   - **IMPORTANT**: Claude session activity WITHOUT corresponding desktop activity should NOT create entries. Claude sessions alone indicate passive/background work that shouldn't be billed.
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

**Activity type separation:**
- Strategic/planning work (OKRs, roadmaps) → separate entries, often "Management" project
- Development work → project-specific entries
- Code review → separate entries with "Code Review" tag, even if time-adjacent to development
- PR-related window titles need explicit verification against Claude sessions to distinguish review from development

**Interpreting desktop activity:**
- Don't accept large time blocks from app names alone (e.g., "Shopping" app doesn't mean 63 minutes of shopping)
- Cross-check significant desktop activity against Claude sessions
- "Chrome" could be docs, PRs, research - not just "browsing"
- "Terminal" could be anything - verify with session content
- Brief scattered activity shouldn't become a consolidated block unless Claude session confirms

**Mandatory entry splitting:**
- Never create entries > 2 hours without explicit user approval
- ALWAYS split when work crosses different projects
- ALWAYS split when work crosses different tags
- Prefer multiple smaller entries over large mixed-work blocks
- When Claude session shows project/context switch mid-block, create separate entries

### Phase 5: User Confirmation

**⏸️ STOP AND WAIT FOR USER INPUT**

9. **Present the gaps** in a table:

| Time | Duration | Description | Project | Tags | Source |
|------|----------|-------------|---------|------|--------|
| 09:15-10:30 | 1h 15m | Code review for auth PR | vivaria | Code Review | Claude session |
| 14:00-14:45 | 45m | Similar to "standup prep" | meetings | Meeting | Pattern match |
| 16:30-17:15 | 45m | Development work | (unknown) | — | Desktop activity |

**⚠️ Warning**: If any proposed entry is missing a project or tags, highlight it and ask the user to provide values before proceeding.

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

12. **Check for overlaps**:
    - Before creating, check if proposed entry overlaps an existing one
    - If overlap found, **report to user** with details:
      - Proposed: 09:00-10:30 "Development work"
      - Existing: 09:45-10:15 "Standup"
      - Overlap: 30 minutes
    - Propose resolution (trim proposed entry to avoid overlap)
    - Create only after user confirms
    - Never modify existing entries—only adjust proposed ones

13. **Report results**:
    - Number of entries created
    - Total time filled
    - Any entries that couldn't be created (and why)

## Completion Criteria

**You are NOT DONE until ALL of the following are verified:**

1. **Desktop activity coverage**: Every period with desktop activity (non-idle) has a time entry
2. **No session-only entries**: No entries exist for periods with Claude activity but no desktop activity (session-only = passive work)
3. **Single-tag rule**: EVERY entry in the date range has exactly ONE tag (not zero, not multiple). This includes pre-existing entries.
4. **Complete metadata**: Every entry has description, project, AND exactly one tag
5. **No overlaps**: No two entries overlap in time
6. **Granularity**: No entry exceeds 2 hours without explicit user approval
7. **Session alignment**: Entry descriptions accurately reflect Claude session content for that period

**Verification loop**:
- After each batch of changes, re-fetch ALL data and re-check ALL criteria
- If any criterion fails, fix it and loop again
- Continue until a full pass with ZERO issues
- Do NOT ask user "are we done?" - verify programmatically

### Phase 7: Verification & Iteration

**Repeat until all completion criteria pass:**

14. **Gap check**: Fetch fresh Toggl entries and desktop activity. Identify any remaining gaps where activity occurred but no entry exists.

15. **Quality check**: For each Toggl entry in the date range, verify:
    - Has non-empty description
    - Has project assigned
    - Has exactly one tag (not zero, not multiple)

    Report any entries missing these and propose fixes.

16. **Overlap check**: Scan all entries for time overlaps. Report any overlapping pairs with details.

17. **Session alignment check**: Cross-reference entries against Claude sessions to verify descriptions make sense. Flag mismatches (e.g., entry says "development" but session shows code review).

18. **Granularity check**: Flag any entries exceeding 2 hours for user review.

19. **Present findings** to user. If issues found:
    - Propose new entries for gaps
    - Propose updates for incomplete entries
    - Propose fixes for overlaps (trim/split)
    - Propose splits for oversized entries
    - Ask for approval before making changes

20. **Create/update entries** as approved and return to step 14.

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
