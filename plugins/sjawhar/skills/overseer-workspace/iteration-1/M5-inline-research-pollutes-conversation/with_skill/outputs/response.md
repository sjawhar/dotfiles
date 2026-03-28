# M5 Response: Inline Research Pollutes Conversation

## What I Would Say to the User

"On it — I'm dispatching two agents right now: one to search your Ghost Whisper transcripts for today's meetings, and another to check Google Drive for any meeting notes or docs from today. I'll have a summary for you in a moment."

## What I Would Do

Dispatch **two background sub-agents in parallel** — NOT run searches inline.

### Why Sub-Agents, Not Inline

The user is in **voice mode**. The overseer skill is explicit:

> "Use sub-agents for ALL non-conversational work."
> "In voice mode especially, every tool call and its output gets read aloud. Keep the main thread clean."

If I searched Ghost Whisper and Google Drive inline, the user would hear:
- Tool call to load `ghost-wispr` skill
- Raw Ghost Whisper API responses with transcript fragments
- Tool call to load `google-workspace` skill
- Raw Google Drive file listings and document contents
- Multiple follow-up searches with keyword variations

That's minutes of incomprehensible tool output read aloud. The main thread should contain only my conversation with the user.

### Sub-Agent 1: Ghost Whisper Search

```
mcp_call_omo_agent(
  subagent_type="explore",
  run_in_background=true,
  description="Search Ghost Whisper transcripts",
  prompt="""
    Load the ghost-wispr skill. Search today's meeting transcripts for:
    1. Decisions made (semantic search: "decided", "agreed", "going with", "approved")
    2. Action items assigned (semantic search: "will do", "action item", "follow up", "by Friday")
    3. Key topics discussed

    Start broad with semantic search, then narrow down with keyword variations.
    Return a structured summary: meeting name/time, decisions made, action items, key context.
  """
)
```

### Sub-Agent 2: Google Drive Search

```
mcp_call_omo_agent(
  subagent_type="explore",
  run_in_background=true,
  description="Check Google Drive meeting docs",
  prompt="""
    Load the google-workspace skill. Search Google Drive for:
    1. Documents modified today that look like meeting notes
    2. Shared docs with "meeting", "notes", "standup", "sync" in the title from today
    3. Any Google Docs with recent edits containing decisions or action items

    Return a structured summary: document name, key decisions, action items, participants if visible.
  """
)
```

### After Both Agents Return

I would synthesize their findings and present a single, clean summary to the user:

"Okay, here's what I found from today. You had [N] meetings. The big decisions were: [decision 1], [decision 2]. There are [N] action items — the most urgent one is [X]. Want me to go deeper on any of these?"

## What I Would NOT Do

- **NOT** load the `ghost-wispr` skill in the main thread
- **NOT** load the `google-workspace` skill in the main thread
- **NOT** run any search/API calls inline where their raw output would be read aloud
- **NOT** dump raw transcript text or document contents into the conversation
- **NOT** make the user sit through multiple rounds of search refinement

## Principle

The overseer's main thread is a conversation. Everything else — gathering, searching, polling, reading — happens in sub-agents. The user hears only the synthesis, never the research process.
