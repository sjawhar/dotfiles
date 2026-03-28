# Response: Worker Stuck on jj Bookmark Conflict + oh-my-opencode Sync Nudge

## What I Would Do

**I would NOT fix either issue myself.** The overseer hard rule is explicit: *you never act directly.* No running `jj bookmark forget`, no running `jj bookmark set`, no describing changes, no pushing. I'm the coordinator, not an IC.

## Concrete Actions

### 1. Dispatch a sub-agent to unstick the bookmark worker

I'd send a sub-agent (via `prompt_async` or equivalent) to the stuck worker's session with a clear, actionable message containing the exact fix:

> "You're hitting a jj bookmark conflict. Run `jj bookmark forget <bookmark-name>` to clear the conflicted state, then `jj bookmark set <bookmark-name>` to re-set it cleanly. Then retry your push with `jj git push`."

The sub-agent delivers the message. The worker executes it. I don't touch jj.

### 2. Dispatch a sub-agent to nudge the oh-my-opencode sync worker

Simultaneously (no dependency between the two), I'd dispatch a second sub-agent to the sync worker's session:

> "Your changes look ready. Describe them with `jj describe -m '<appropriate message>'` and push with `jj git push`."

### 3. Report to the user

After both sub-agents confirm delivery, I'd tell the user:

> "Two workers were stalled. Sent the bookmark conflict fix to the first worker (forget + re-set). Nudged the oh-my-opencode sync worker to describe and push. Both should be moving again shortly — I'll check on them next turn."

### 4. Add to Watch List

Update the briefing's Watch List to check both workers on the next gather cycle, confirming the fixes landed.

## Why Not Do It Myself?

Even though I know the exact commands and could resolve both issues in seconds:

- **The hard rule exists for a reason.** The overseer thread is the user's conversation channel. Every tool call pollutes it — especially in voice mode where outputs get read aloud.
- **Workers have their own context.** They know their working directory, their bookmark names, their change descriptions. Sending them the fix pattern lets them apply it correctly in their context.
- **It's not my role.** Workers are individual contributors. I'm the coordinator. Doing the work myself breaks the separation that makes the whole system legible.

The temptation is real — it's two commands, I know the fix, it would be faster. But "faster for this one thing" erodes the discipline that makes the system work at scale.
