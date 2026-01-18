# Weekly Review

Interactive weekly reflection with Toggl integration and historical trend analysis.

**Run on Sundays** - Reviews the previous week (Sunday through Saturday).

## Arguments

**Required** (first argument): Path to JSONL data file
- Example: `/weekly-review ~/app/ignore/weekly-reviews.jsonl`

The file will be created if it doesn't exist. Data is appended (one JSON object per line).

## Quick Reference

1. **Trends**: Show 8-week historical patterns with bar charts
2. **Toggl**: Fetch and aggregate time entries by project/activity
3. **Context**: Present pre-filled data, prompt for narration
4. **Narrate**: User speaks freely, Claude tracks and maps to structure
5. **Structure**: Present filled form, highlight gaps
6. **Challenge**: Red team pushes back on patterns and blind spots
7. **Save**: Append to JSONL file

## Critical Constraints

These rules take priority over other instructions:

1. **Don't condense or editorialize user prose** - When organizing user's words into sections, preserve their full text (though removing filler words is good). Don't summarize "I'm still not prioritizing PMing monitorability enough. I'm not attending stand-ups yet" into "Not prioritizing well". The detail enables future red-teaming.

2. **Preserve full detail for future analysis** - Specific names, events, dates, and context are the value. Future red-teaming depends on being able to reference "Beth" or "Tuesday's planning meeting" - generic summaries lose this.

3. **Confirm before phase transitions** - Always confirm with the user before moving from narration (Phase 4) to structured presentation (Phase 5). Don't assume "done" from ambiguous signals.

## Argument Parsing

The JSONL path is passed as the skill's `args` parameter:

```
/weekly-review ~/path/to/weekly-reviews.jsonl
```

**Handling**:
- If `args` is empty or missing, prompt the user: "Please provide a path to your weekly reviews JSONL file"
- Expand `~` to the user's home directory (use `$HOME` or equivalent)
- Create the file if it doesn't exist (first review will have no historical trends)

## Phase 1: Trend Analysis

1. **Load historical data** from the provided JSONL path
   - If file doesn't exist, start fresh (no historical trends to show)
   - Parse last 8 weeks of entries

2. **Generate trend visualizations** using `plotext` via `uvx`:

```bash
uvx --with plotext python -c "
import plotext as plt

weeks = ['Nov23', 'Nov30', 'Dec7', 'Dec14', 'Dec21', 'Dec29', 'Jan4', 'Jan11']
development = [30, 30, 40, 20, 30, 50, 40, 45]

plt.bar(weeks, development)
plt.title('Development ↗ up')
plt.ylim(0, 60)
plt.plotsize(40, 12)
plt.theme('clear')
print(plt.build())
" | sed 's/\x1b\[[0-9;]*m//g'
```

Output:
```
             Development ↗ up           
  ┌────────────────────────────────────┐
60┤                                    │
50┤                      █████         │
40┤         █████        ██████████████│
30┤██████████████    ██████████████████│
  │██████████████    ██████████████████│
20┤████████████████████████████████████│
10┤████████████████████████████████████│
 0┤████████████████████████████████████│
  └──┬────────┬────────┬───┬────────┬──┘
   Nov23    Dec7   Dec21 Dec29   Jan11  
```

**For each major activity/rating**, generate a separate chart. Show 3-4 charts that are most relevant:
- Activities with biggest changes (up or down trends)
- Any ratings that hit unusually low values
- The user's top time-consuming activities

**Annotate anomalies** in the title or after the chart:
- "Mental: 3 ← lowest in 8 weeks"
- "Development ↗ trending up (avg 35%, last week 45%)"

3. **Detect patterns**:
   - Recurring themes in bottlenecks/mistakes (look for repeated words/phrases)
   - Correlation between low ratings and specific activities
   - Priority tracking: did last week's priorities appear in this week's successes?
   - Surface anomalies: "You've mentioned sleep issues in 3 of the last 5 weeks"

4. **Present trends**: "Here's how your past 8 weeks looked. Keep this in mind as you reflect."

## Phase 2: Toggl Data Collection

1. **Determine user's timezone**:
   - Check system timezone or ask user if unclear
   - Default assumption: **US Pacific (America/Los_Angeles)**

2. **Calculate date range in user's local timezone**:
   - End: Yesterday (Saturday) at 23:59:59 local
   - Start: Sunday of the previous week at 00:00:00 local
   - Example: If today is Sunday Jan 18 Pacific, range is Jan 11 00:00 - Jan 17 23:59 Pacific

3. **Convert to UTC for Toggl API query**:
   - Toggl stores times in UTC, so expand the query range to capture all local-time entries
   - For Pacific time: query from start_date (local Sunday) to end_date + 1 day (UTC)
   - Example: Jan 11-17 Pacific → query Toggl for Jan 11-18 UTC
   - This ensures entries logged late Saturday Pacific (which is already Sunday UTC) are included

4. **Fetch time entries** using `toggl_get_time_entries` with the expanded UTC range

5. **Filter results to local timezone window**:
   - Convert each entry's start time to user's local timezone
   - Exclude entries outside the local date range (e.g., entries on Jan 19 UTC that are actually Jan 18 Pacific stay; entries that are Jan 19 Pacific get excluded)

6. **Aggregate by project and tag** to compute actual time percentages

7. **Map to categories**:

**Activities** (map from Toggl tags):
- Admin, Code Review, Data, Design, Development, Events
- Meetings, Messages, Ops, Planning, Reading, Research
- Training, Writing, Other

**Projects** (map from Toggl projects):
- 3PRA, Capabilities, Data Pipeline, Hiring, Human Uplift
- Infra, Inspect Action, Managing, Middleman, Monitorability
- Org, Tasks and Agents, Vivaria, Other

Use fuzzy matching for project/tag names (case-insensitive, partial match).

## Phase 3: Present Context

1. **Show computed time allocations** as pre-filled tables:

```
Activities (from Toggl):
  Development: 35%
  Meetings: 20%
  Code Review: 15%
  ...
```

2. **Display question sections** for reference:
   - Time allocation (activities and projects)
   - Goal tracking checkboxes
   - Reflection questions (successes, mistakes, bottlenecks)
   - Self-assessment ratings (1-7 scale)
   - Next week priorities

3. **Prompt**: "Toggl says you spent X% on Development, Y% on Meetings. Does that feel right? Start narrating your thoughts - I'll track them."

## Phase 4: Free-Form Narration

**Let the user speak/type freely.** Track and map to form sections:

- **Time corrections**: "Actually spent more time on Ops than Toggl shows" → adjust activities
- **Goal tracking**: "I paired with Thomas on Tuesday" → mark checkbox
- **Reflections**: "The deploy went smoothly" → successes; "Should have communicated earlier" → mistakes
- **Ratings**: "Productivity was maybe a 5" → track rating
- **Priorities**: "Next week I need to focus on the viewer" → next_week.priorities

**Brief acknowledgments**: "Got it - tracking as a bottleneck" (keep responses minimal)

**CRITICAL: Don't condense or editorialize.** When organizing user's words into sections:
- Reorganize into the appropriate fields, but preserve the user's full prose
- Don't summarize "I'm still not prioritizing PMing monitorability enough. I'm not attending stand-ups yet, I don't have daily syncs with Lawrence" into "Not prioritizing well"
- The detail is the value - future red-teaming depends on specific names, events, and context
- If the user gives bullet points, keep them as bullet points
- If the user gives long prose, keep it as long prose

**Exit condition**: When user signals completion ("done", "ready", "that's it", or similar):
1. Summarize what you've captured: "I have: 3 successes, 2 mistakes, 1 bottleneck, ratings for mental/productivity/engagement..."
2. Ask for confirmation: "Ready to see the structured form, or want to add more?"
3. Only proceed to Phase 5 after explicit confirmation

## Phase 5: Structured Presentation

1. **Present the filled-in form** with all sections organized:

```
## Time Allocation

### Activities
- Development: 30-40% (Toggl: 35%)
- Meetings: 20-30% (Toggl: 20%)
...

### Projects
- Inspect Action: 40-50%
- Data Pipeline: 20-30%
...

## Goals
✓ Paired with an engineer
✓ Left comments on writeups
✗ Read team writeups
Notes: "Paired with Thomas on the auth bug"

## Reflection
Successes: Got the feature shipped ahead of schedule...
Mistakes: Should have communicated the timeline change earlier...
Bottlenecks: Waiting on PR reviews blocked me for a day...
Action: Set up office hours for quick reviews
Priorities check: Yes, working on the highest impact item
Iteration: Add a daily standup check-in

## Ratings
Mental: 5  Productivity: 6  Prioritization: 5
Time mgmt: 4  Engagement: 5  Overall: 5

## Next Week
Priorities: 1. Ship viewer  2. Onboard Rafael  3. Plan Q1
On track (project): Yes
On track (personal): Unsure
```

2. **Highlight gaps**: "Missing: What's one thing you'll change next week?"

3. **Show discrepancies**: "Your perceived Meeting time (30-40%) vs Toggl (25%)"

4. **Ask for edits**: "Anything to adjust before we continue?"

## Phase 6: Red Team Challenge

**Launch the `red-teamer` agent** (using Task tool with `subagent_type: "red-teamer"`).

**Context to pass to the agent** (include all of this in the prompt):
1. **Current week data**: The complete structured form from Phase 5 (time allocations, reflections, ratings, priorities)
2. **Historical trends**: Summary of last 8 weeks - recurring themes in successes/mistakes/bottlenecks, rating patterns, priority follow-through
3. **Repeated patterns**: Phrases or themes that appear in multiple weeks (e.g., "communicate better" appearing 3 times)

**Challenge areas for the agent to probe**:
- Compare self-assessment vs objective Toggl data
- Recurring bottlenecks that haven't been addressed
- Priorities that don't align with actual time spent
- Patterns from history: "You've said 'communicate better' for 3 weeks - what's blocking that?"
- Blind spots: activities consuming time without mention

**Present challenges** to the user. Let them respond and revise if needed.

## Phase 7: Save

1. **Build the JSON object** with all collected data (see schema below)
   - Include `red_team` with challenges raised and user responses
   - Ensure `week.start` and `week.end` are filled (YYYY-MM-DD)
   - Don't abbreviate reflection fields - preserve the user's full prose as they said it

2. **Show the user what will be saved** before saving
   - Display the reflection fields in full (these are the most important)
   - Ask: "Does this capture everything? Ready to save?"

3. **Append to the provided JSONL file**
   - Each line is one complete JSON object
   - Append with newline separator

4. **Show summary**: "Week of Jan 12-18 saved. Review #31."

## JSONL Schema

Each line in `weekly-reviews.jsonl` is a JSON object:

```json
{
  "timestamp": "2026-01-19T10:30:00Z",
  "week": {"start": "2026-01-12", "end": "2026-01-18"},
  "activities": {
    "admin": "0-5", "code_review": "10-20", "data": "0",
    "design": "0-5", "development": "40-50", "events": "0",
    "meetings": "10-20", "messages": "0-5", "ops": "0-5",
    "planning": "0-5", "reading": "5-10", "research": "0",
    "training": "0", "writing": "5-10", "other": "0"
  },
  "projects": {
    "3pra": "0", "capabilities": "0", "data_pipeline": "20-30",
    "hiring": "0-5", "human_uplift": "0", "infra": "0-5",
    "inspect_action": "30-40", "managing": "5-10", "middleman": "0",
    "monitorability": "0", "org": "10-20", "tasks_agents": "5-10",
    "vivaria": "0", "other": "0"
  },
  "toggl": {
    "activities": {"development": 35, "meetings": 20, "code_review": 15},
    "projects": {"inspect_action": 40, "data_pipeline": 25}
  },
  "goals": {
    "completed": ["paired_engineer", "read_writeups", "left_comments"],
    "notes": "Paired with Thomas on the auth bug"
  },
  "reflection": {
    "successes": "The foobar feature went really well, users are really happy with it. I think we...",
    "mistakes": "I should have been more patient with Jaime when they asked about timelines. Maybe I could...",
    "bottlenecks": "Waiting on PR reviews blocked me for a day. The auth PR has been open for a week now...",
    "bottleneck_action": "Set up office hours for quick reviews",
    "priorities_check": "Yes, working on the highest impact item",
    "iteration": "Add a daily standup check-in"
  },
  "ratings": {
    "mental": 5, "productivity": 6, "prioritization": 5,
    "time_mgmt": 4, "engagement": 5, "overall": 5
  },
  "next_week": {
    "priorities": ["Ship viewer", "Onboard Rafael", "Plan Q1"],
    "on_track_project": "yes",
    "on_track_personal": "unsure"
  },
  "optional": {
    "prompt": "What surprised me this week?",
    "response": "How much energy I had on Tuesday"
  },
  "red_team": {
    "challenges": "You've mentioned 'communicate better' for 3 weeks now - what's concretely blocking that?",
    "user_responses": "I think I need to add a buffer before responding to pushback..."
  }
}
```

**Field notes**:
- **`reflection.*`**: Preserve the user's full prose - don't condense "I'm still not prioritizing PMing monitorability enough. I'm not attending stand-ups yet" into "Not prioritizing well". The detail enables future red-teaming.
- **`red_team`**: Captures the red team challenges and user's responses for future reference
- **Time semantics** - two different representations:
  - `activities` and `projects`: User's *perceived* time allocation (ranges like "0-5", "10-20", "40-50"). These reflect how the user *felt* they spent their time, adjusted from Toggl data based on narration.
  - `toggl.*`: *Actual* recorded time from Toggl (exact integers). Raw data, not adjusted.
  - The gap between these is valuable for red-teaming ("You felt you spent 30% on meetings but Toggl shows 20%")
- `goals.completed`: Array of checkbox IDs that were checked
- `ratings`: Integer 1-7 scale
- `next_week.on_track_*`: "yes", "no", or "unsure"
- **`optional`**: For ad-hoc reflection prompts. The `prompt` field is the question asked, `response` is the user's answer. Use when exploring specific themes not covered by standard fields.
- **`week.start` and `week.end`**: Always fill these (YYYY-MM-DD format) - don't leave empty

## Fallback Handling

### Toggl Failure
If `toggl_get_time_entries` is unavailable or fails:
1. Inform the user: "Toggl integration unavailable - we'll do manual time entry"
2. Skip Phase 2 entirely
3. In Phase 3, present empty time allocation tables
4. Ask user to estimate percentages directly: "How did you spend your time this week? Estimate percentages for your main activities."

### Chart Failure
If `plotext` fails or produces garbled output:
1. Fall back to text-based visualization:
```
Development:  ████████████████████ 40%
Meetings:     ██████████ 20%
Code Review:  ███████ 15%
Writing:      █████ 10%
Other:        ███████ 15%
```
2. Use Unicode block characters (█) scaled to percentage
3. Still show trend indicators: "↗ up from 30%" or "↘ down from 50%"

## Edge Cases

- **Missing JSONL file**: Start fresh, no historical trends to show
- **Malformed JSON lines**: Skip and warn, continue with valid data
- **No Toggl data**: Show empty tables, proceed with manual entry (see Fallback Handling)
- **User skips sections**: Fill with empty values, note gaps
- **Not Sunday**: Warn but allow running anyway ("Running mid-week - date range may be unexpected")

## Goal Tracking Checkboxes

Standard goals to track (user can mention any of these):

- `paired_engineer`: Paired with an engineer this week
- `read_writeups`: Read team members' writeups
- `left_comments`: Left comments on others' work
- `gave_feedback`: Gave constructive feedback
- `asked_help`: Asked for help when stuck
- `documented`: Documented decisions or learnings
- `mentored`: Mentored or helped onboard someone
- `shipped`: Shipped something to users

## Notes

- Uses `toggl_get_time_entries` MCP tool for Toggl integration
- Red team uses Task tool with `subagent_type: "red-teamer"`
- **Timezone**: Default to US Pacific. Query Toggl with expanded UTC range, then filter to local timezone.
- **Charts**: Use `uvx --with plotext python` for terminal bar charts (no install needed)
- JSONL format allows easy appending and parsing
