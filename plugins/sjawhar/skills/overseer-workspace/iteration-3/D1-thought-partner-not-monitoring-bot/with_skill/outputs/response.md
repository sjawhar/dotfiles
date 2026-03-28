# Overseer Response — "Be my chief of staff today"

## What I Would Do (Internal Actions)

### Step 1: Silent Context Gathering via Sub-Agents

I'd dispatch **5-6 background sub-agents in parallel** to gather context. The user sees none of this — no tool calls read aloud, no raw output cluttering the conversation.

- **Sub-agent 1 (briefing + memory):** Read `~/.dotfiles/.overseer/briefing.md` and `~/.dotfiles/.overseer/memory.md` — what did the previous overseer leave me? What institutional knowledge do I have?
- **Sub-agent 2 (priorities):** Scan `~/.dotfiles/.overseer/priorities/` for any user-provided priority docs — sprint plans, deadline info, strategic context.
- **Sub-agent 3 (running sessions):** Run `bash -ic 'oc ps'`, then check session statuses via the serve API. Not to list them — to understand what work is actually in flight and whether it's healthy.
- **Sub-agent 4 (Ghost Whisper):** Load the `ghost-wispr` skill and search for recent voice context — priority discussions, decisions, anything the user said out loud but didn't write down. Start broad with semantic search, narrow down.
- **Sub-agent 5 (GitHub/Linear):** Check PR state and issue state — what's merged, what's in review, what's stuck.
- **Sub-agent 6 (Google Workspace):** Check for today's standup notes or planning docs.

### Step 2: Synthesize Results

Once the sub-agents report back, I'd synthesize across all sources:
- What work is actively progressing?
- What's stuck or needs attention?
- What did the previous overseer flag?
- What are the user's stated priorities (from docs and voice)?
- Where are the gaps between priorities and active work?

### Step 3: Respond to the User

---

## What I Would Say to the User

> Morning. Let me get oriented — give me a moment to pull everything together.

*[Brief pause while sub-agents complete]*

> Alright, here's where things stand.
>
> **The previous overseer left a note** that [X workstream] was the main focus, with [Y] as a secondary priority. They flagged [specific thing] as needing your input.
>
> **From your priority docs**, [key priority A] and [key priority B] are what you've marked as most important right now.
>
> **On the work side:** [Feature X] has made solid progress — [specific metric, e.g., "8 of 12 issues closed, 3 PRs merged"]. [Feature Y] is earlier — [specific state]. There's nothing actively broken, but [specific thing] looks like it might need a nudge.
>
> **I also caught [something from Ghost Whisper]** — you mentioned [relevant context from a voice conversation] yesterday. That might affect how we prioritize [specific area].
>
> **The biggest gap I see** is [priority that has no active work on it / mismatch between stated priorities and running work]. And [specific thing] is the highest-leverage item that's not getting attention right now.
>
> What's on your mind? Are those priorities still right, or has something shifted? Once I know where your head's at, I'll map out a plan for what I'm watching and where I'll push things forward.

---

## Why This Approach (Not the Alternative)

### What I did NOT do:

- ❌ **List sessions by port, directory, or ID.** ("Session ses_abc123 on port 38465 in ~/workspace/foo...") — The user doesn't care about plumbing. They care about what work is happening and whether it's progressing.
- ❌ **Dump raw status data.** ("13 workers active, 3 idle, 2 errored") — Activity indicators aren't status. Status is progress against priorities.
- ❌ **Start monitoring robotically.** ("I'll now begin monitoring your sessions and report back periodically") — That's a bot. The user asked for a chief of staff.
- ❌ **Explain how Legion or the overseer system works.** The user built it. They know.
- ❌ **Ask permission to gather context.** ("Should I check your sessions?") — That's my job. Just do it.
- ❌ **Make tool calls in the main thread.** Everything goes through sub-agents so the conversation stays clean.

### What I did instead:

- ✅ **Gathered context silently** — sub-agents do the legwork, I synthesize.
- ✅ **Opened with what matters** — previous handoff, priorities, progress against those priorities, gaps.
- ✅ **Framed everything against the user's priorities** — not against system state.
- ✅ **Named the biggest gap** — proactively surfaced the highest-leverage insight.
- ✅ **Included voice context** — Ghost Whisper often has priority discussions that aren't written down.
- ✅ **Asked what's on their mind** — this is the collaborative part. I bring the operational picture, they bring the strategic intent. Together we figure out what matters.
- ✅ **Set up the next step** — "once I know where your head's at, I'll map out a plan." Not "I'll continue monitoring." A plan we build together.

### The Core Distinction

A **monitoring bot** reports system state and asks what to do next.

A **thought partner** brings the full picture, names the gaps, and collaborates on what matters. The user said "help me figure out what to focus on" — that's a thinking-together request, not a monitoring request. The response opens a conversation about priorities and strategy, grounded in concrete operational data.
