# C2 Response: Main Thread Tool Use (With Skill)

## What I Would Say to the User

"Checking on both now — give me a sec."

Then, after results come back:

A brief conversational summary of what each worker is doing, what's healthy, what needs attention. No session IDs, no ports, no directories. Feature names only.

## How I Would Check

**Dispatch two sub-agents in parallel** — one for the web UI worker, one for the Legion controller. Both run in background.

### Why Sub-Agents, Not Inline

Three reasons, all from the skill:

1. **Sub-agent discipline rule**: "Use sub-agents for ALL non-conversational work. Polling, status checks, message sending, information gathering, session reads — everything that isn't the overseer talking directly to the user happens in sub-agents."

2. **Voice mode**: "In voice mode especially, every tool call and its output gets read aloud. Keep the main thread clean." Running `oc ps`, then API calls to read session messages, then parsing JSON — all of that would get read aloud as garbled tool output. Unusable.

3. **Parallel efficiency**: Two independent checks with no dependency between them. Sub-agents run simultaneously. Inline would be sequential.

### What Each Sub-Agent Would Do

**Sub-agent 1: Web UI worker**
- Run `bash -ic 'oc ps'` to find the session
- Hit the serve API to read recent messages
- Check for signs of progress, stalling, or errors
- Report back: what it's working on, last meaningful action, any blockers

**Sub-agent 2: Legion controller**
- Same flow — `oc ps`, serve API, read recent messages
- Additionally check: how many workers it's managing, any stuck dispatches, queue state
- Report back: what it's coordinating, worker health summary, any pending decisions

### After Results Return

Synthesize both reports into a single conversational update. Voice-friendly — no tables, no lists, no IDs. Something like:

"The web UI worker is making good progress on the settings page redesign — it just finished the layout component and is working on the form validation. The Legion controller has three workers running right now, all healthy. It dispatched a new one for the auth refactor about ten minutes ago."

Then surface anything that needs attention or a decision.

## Key Principle Demonstrated

The main thread stays conversational. All investigation happens in sub-agents. The user hears a clean summary, not raw API output.
