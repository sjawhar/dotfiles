---
description: "Analyze Claude Code sessions for patterns and improvements"
---

Analyze my Claude Code session transcripts to identify patterns in corrections and feedback, then generate actionable improvements.

## Arguments

Optional: Number of days to analyze (default: 7)

Example: `/reflect 14` to analyze the past 2 weeks.

## Phase 1: Setup

**1.1 Load previous report** (if exists):
- Check for latest report in `~/.dotfiles/.claude/session-analysis/`
- Extract previous improvements and expected results for comparison
- If no previous report exists, note this is the first run

**1.2 Run indexing**:
```bash
python3 ~/.dotfiles/.claude/skills/reflect/index-sessions.py --days {N}
```

**1.3 Query flagged turns** from SQLite database at `~/.dotfiles/.claude/sessions.db`:

```python
import sqlite3
conn = sqlite3.connect(str(Path.home() / '.dotfiles' / '.claude' / 'sessions.db'))

# Get all flagged turns from the past N days
query = '''
SELECT
    s.project, s.id as session_id, s.source_path, s.initial_prompt_preview,
    t.turn_number, t.type, t.line_start, t.line_end,
    f.flag_type
FROM flags f
JOIN turns t ON f.turn_id = t.id
JOIN sessions s ON t.session_id = s.id
WHERE s.timestamp > datetime('now', '-{N} days')
ORDER BY s.timestamp DESC
'''
```

## Phase 2: Content Extraction

**2.1 Fetch flagged content** from source files using line numbers:

For each flagged turn, read the content from the source JSONL file using the `line_start` and `line_end` values. The indexing script provides a `fetch_turn_content()` function that handles this.

**2.2 Apply secret redaction** before agent access:
- API keys (sk-*, ANTHROPIC_API_KEY, etc.)
- GitHub tokens (ghp_*, gho_*)
- AWS credentials
- Passwords and secrets
- Bearer tokens
- Private keys

**2.3 Group by session** for context preservation:
- Include initial_prompt_preview for each session
- Include surrounding turns (1-2 turns before/after flagged turn) for context
- Write to temporary markdown files for agent consumption

## Phase 3: Parallel Agent Analysis

Launch 5 specialized agents **in parallel** using the Task tool:

| Agent | Mandate |
|-------|---------|
| **Mistake Finder** | Find Claude errors that user corrected: wrong file, wrong approach, misunderstood request. Focus on `rejection` and `interrupt` flags. |
| **Preference Learner** | Identify implicit user preferences: formatting, communication style, tool choices, workflow patterns. Look for repeated `clarification` patterns. |
| **Command Repeater** | Find repeated slash commands or multi-step workflows that could become skills. Look for patterns in initial prompts. |
| **Prompt Repeater** | Find similar initial prompts across sessions suggesting a skill opportunity. Use `initial_prompt_preview` from sessions table. |
| **CLAUDE.md Miner** | Find project-specific concepts, patterns, or rules that should be documented. Group findings by project. |

**Agent prompt template**:
```
You are analyzing Claude Code session transcripts to find {AGENT_FOCUS}.

Context: {PREVIOUS_REPORT_SUMMARY}

Here are the flagged turns from the past {N} days:

{FLAGGED_CONTENT_MARKDOWN}

For each pattern you find:
1. Describe the pattern clearly
2. Provide 2-3 specific examples with session IDs
3. Explain the root cause
4. Suggest a concrete improvement (skill, CLAUDE.md update, alias, etc.)

Focus on actionable patterns that appear at least 2-3 times.
```

## Phase 4: Consolidation

After all agents complete:

**4.1 Deduplicate findings**:
- Merge similar patterns identified by multiple agents
- Group by improvement type: skill, CLAUDE.md, alias, agent, prompt change

**4.2 Assess previous improvements** (if previous report exists):
- For each prior improvement with an "expected result", check if current data shows improvement
- Mark as: improved, unchanged, or regressed

**4.3 Rank by frequency and impact**:
- Count how many sessions each pattern appears in
- Prioritize patterns that cause most corrections/interrupts

## Phase 5: Interactive Presentation

Present findings to user for decision:

```
## Pattern: {PATTERN_NAME}
Frequency: {COUNT} sessions
Type: {IMPROVEMENT_TYPE}
Root cause: {ANALYSIS}

Examples:
- Session abc123: "{example_excerpt}"
- Session def456: "{example_excerpt}"

Suggested improvement:
{DETAILED_SUGGESTION}

Options:
1. Implement now
2. Defer (add to next week's list)
3. Dismiss (not actionable)
```

For each pattern, ask the user to choose using the AskUserQuestion tool with options.

## Phase 6: Execute Approved Changes

For each "implement now" decision:

**Skills**: Create new file in `~/.dotfiles/.claude/commands/{skill-name}.md`

**CLAUDE.md updates**:
- Global: Edit `~/.dotfiles/.claude/CLAUDE.md`
- Project-specific: Edit `~/.dotfiles/.claude/project-instructions/{project}/CLAUDE.md`

**Bash aliases**: Add to `~/.dotfiles/bash/aliases.sh`

**Agents**: Create new file in `~/.dotfiles/.claude/agents/{agent-name}.md`

## Phase 7: Generate Report

Save report to `~/.dotfiles/.claude/session-analysis/YYYY-MM-DD.md`:

```markdown
# Session Analysis Report - {DATE}

## Summary
- Sessions analyzed: {COUNT}
- Date range: {START} to {END}
- Projects covered: {PROJECT_LIST}
- Flags detected: {interrupt: N, rejection: N, clarification: N}

## Previous Improvements Assessment
{For each prior improvement: status (improved/unchanged/regressed), evidence}

## Patterns Observed
{For each pattern: frequency, examples, root cause, suggested improvement}

## Improvements Made
{For each implemented change: type, file, reason, expected result}

## Improvements Deferred
{For each deferred: reason, trigger to revisit}

## Notes for Next Analysis
{Context that future runs should consider}
```

## Fallback Handling

- **Empty database**: Run indexing first, report if no sessions found
- **No flags detected**: Report healthy session patterns, no corrections needed
- **Agent failures**: Continue with remaining agents, note failures in report
- **Large data volumes**: If >100 flagged turns, chunk into groups of 30 per agent call

## Done When

1. Report is generated and saved
2. All approved improvements are implemented
3. User has reviewed all significant patterns
