# Overseer Response: Meeting Decisions Check

## What I would say to the user (voice)

> "On it — checking Ghost Whisper transcripts and your Drive docs now. Give me a moment."

## What I would do: Dispatch two sub-agents in parallel

**I would NOT run any searches inline in the main conversation thread.**

The user is in voice mode. Every tool call and its output gets read aloud. Running Ghost Whisper semantic searches and Google Drive queries inline would mean the user hears raw API responses, search result lists, transcript chunks, and document metadata read back to them — destroying the conversational experience.

Instead, I dispatch two background sub-agents simultaneously:

### Sub-agent 1: Ghost Whisper search

- **Agent type**: explore (background)
- **Instructions**: Load the `ghost-wispr` skill. Search today's meeting transcripts for decisions, action items, commitments, and agreements. Start with a broad semantic search like "decision made today" and "agreed to", then narrow down with specific keyword variations: "we decided", "the plan is", "action item", "let's go with", "approved". Extract and summarize any decisions found with context about who said what and in which meeting.

### Sub-agent 2: Google Drive docs

- **Agent type**: explore (background)
- **Instructions**: Load the `google-workspace` skill. Search for documents modified today — meeting notes, agendas, and shared docs. Look for decision logs, meeting minutes, or notes that contain decisions, action items, or outcomes. Summarize findings with document titles and key decisions.

Both agents run in parallel via `mcp_call_omo_agent` with `run_in_background=true`.

## After results come back: Synthesize and present

Once both sub-agents return, I would synthesize their findings into a single conversational summary. In voice mode, this means:

> "Found a few things. In your morning standup, the team decided to push the release to next Thursday — Sarah flagged a blocker on the auth flow. In the product sync, you agreed to cut the dashboard redesign from v2 scope and revisit in Q3. Your meeting notes doc from the 1:1 with Alex has an action item for you to review the hiring plan by Friday. That's the main stuff — want me to dig into any of these?"

## Why sub-agents, not inline

| Concern | Inline research | Sub-agent research |
|---------|----------------|-------------------|
| Voice experience | User hears raw search results, API responses, document metadata | User hears only the synthesized summary |
| Main thread cleanliness | Polluted with tool calls and outputs | Clean conversational flow |
| Parallelism | Sequential — one search blocks the next | Both searches run simultaneously |
| Skill compliance | Violates "Use sub-agents for ALL non-conversational work" | Follows the pattern exactly |

The overseer skill is explicit: *"The main thread is your conversation with the user. In voice mode especially, every tool call and its output gets read aloud. Keep the main thread clean."* And the anti-patterns table lists "Research in the main thread" with the correction "Use sub-agents for all information gathering."
