# Overseer Worked Examples

Mix of real incidents (labeled) and recommended playbooks. Reference material — not prescriptive, but illustrative.

---

## Example 1: Unblocking a Stuck Agent
**Type: Real incident**

**Situation:** The Docker/Taiga build agent hit an MCP timeout. It declared the problem an "infrastructure issue" and stopped making progress.

**What the overseer did:** Sent a prompt invoking the no-excuses pattern, reframing the problem as the agent's responsibility — not something to write off as external. Pointed out that "infrastructure issue" is a forbidden excuse pattern.

**What happened:** The agent stopped blaming infrastructure, dug deeper, and found the actual bug: missing standalone config for new environments. Fixed it.

**Lesson:** Agents will sometimes classify their own failures as external. The overseer should challenge this. A stuck agent that blames the environment is usually a stuck agent that hasn't looked hard enough.

---

## Example 2: Orchestrating a Model/Agent Switch
**Type: Real incident**

**Situation:** The 1Password planner had finished its plan. The user wanted to switch to the Hephaestus agent with GPT 5.4 for implementation.

**What the overseer did:**
1. Sent the planner a prompt to hand off to Hephaestus
2. Monitored for the new session to appear
3. Sent the new session a `prompt_async` with model and agent overrides in the request body

See [reference.md](reference.md) for the `prompt_async` API pattern with model/agent override.

**Lesson:** The overseer can control which model and agent handle work via the `prompt_async` API. This is how you orchestrate handoffs between planning and implementation phases.

---

## Example 3: Catching Bad Information
**Type: Real incident**

**Situation:** The Legion controller's todo board showed KP and BM issues as "in progress." The overseer reported this to the user.

**What happened:** The user caught a contradiction — those issues couldn't be in-progress if all PRs were merged. The overseer verified against GitHub and confirmed all 13 PRs were merged. The todo board was stale.

**Lesson:** Always verify claims against ground truth. Stale caches (todo boards, status fields) are common failure modes. GitHub PR state is authoritative for merge status. When something doesn't add up, check the source of truth before reporting.

---

## Example 4: Monitoring — Passive vs Active
**Type: Recommended playbook**

**Situation:** The user asks you to "keep an eye on the deploy pipeline" while they go to lunch.

**Passive approach** (no mechanism, just a note):
```
"I've added the deploy pipeline to my watch list. I'll check its status each
time we talk. If something breaks, I'll surface it in my next briefing."
```
→ Add an entry to `briefing.md` under Watch List. Check it during the next gather phase.

**Active approach** (actual polling agent):
```
"I've dispatched a background agent to poll the deploy pipeline every 5 minutes.
It'll report back if any stage fails or if the pipeline stalls."
```
→ Launch a sub-agent with instructions to poll and write updates to the briefing.

**The wrong approach:**
```
"I'm monitoring the deploy pipeline and will let you know if anything happens."
```
→ This claims active monitoring without a mechanism. The overseer is paused between messages. Nothing is being monitored. This is dishonest.

**How to choose:**
- User will be back soon + low risk → passive (watch list)
- User will be away + critical work running → active (polling agent)
- You're unsure → be explicit: "I can add this to my watch list for next check-in, or dispatch a polling agent if you want real-time coverage."

---

## Example 5: Prioritizing Environment Bug Fixes
**Type: Recommended playbook**

**Situation:** Multiple environment bugs were discovered during a deploy. The user needed to decide which to fix first.

**What the overseer should do:**
1. List all known bugs with a brief description
2. Classify P0s (actively broken, blocking users) vs P1s (broken but workaround exists)
3. For P1s, estimate downstream effect — how many tasks/scenarios does each bug block?
4. Present a ranked list: P0s first, then P1s sorted by downstream impact
5. Show the reasoning so the user can adjust

**Example output:**
```
Bugs ranked by estimated downstream effect:

P0:
- Standalone server crash on KP/BM — blocks all deploy testing for 2 environments

P1 (by downstream impact):
- CSS injection not applied on reskin — blocks 4 scenarios across 3 features
- Webhook timeout on large payloads — blocks 2 scenarios, workaround exists
- Login redirect loop on Safari — blocks 1 scenario, low traffic browser
```

**Lesson:** Don't just list issues — rank them by impact. P0s first, then sort by how many things each bug is blocking.

---

## Example 6: Sub-Agent Research Pattern
**Type: Recommended playbook**

**Situation:** The user asks "what's the state of things?" at the start of a session.

**What the overseer should do:**
1. Dispatch parallel sub-agents:
   - One to run `oc ps` and check session statuses
   - One to read the briefing notepad and memory file
   - One to scan priority docs directory
   - One to search Ghost Whisper for recent voice context
   - One to check GitHub for open PRs
2. Wait for results
3. Synthesize into a compact briefing
4. Present to the user in the main thread

**What the overseer should NOT do:**
- Run all those queries inline in the main conversation
- Dump raw `oc ps` output to the user
- Read out Ghost Whisper transcripts verbatim

**Lesson:** Research in sub-agents, synthesis in the main thread. This matters most in voice mode where every piece of text gets read aloud.

---

## Example 7: Routing a Bug Report from Slack to Legion
**Type: Real incident**

**Situation:** The user shared a Slack thread URL about a dual-builds RuntimeError. They mentioned it casually — didn't give detailed instructions on what to do with it.

**What the overseer did:**
1. Dispatched a sub-agent that loaded the `slack-bot` skill and read the full Slack thread
2. The sub-agent extracted the bug details: RuntimeError during dual-builds, reproduction steps, affected environments, and the reporter's context
3. Dispatched a second sub-agent to send the full context to the Legion controller with instructions to file a GitHub issue and dispatch a planner
4. The planner was told to communicate with the bug reporter in Slack for additional details if needed

Total time from user mention to worker dispatched: under a minute.

**What the overseer did NOT do:**
- Ask the user "should I send this to Legion?"
- Ask for clarification on the bug details — the Slack thread had everything
- Send a lossy one-line summary to the controller — full context was forwarded

**Lesson:** When the user surfaces a bug, route it immediately with full context. Don't ask for permission, don't ask for details you can get yourself, and don't compress the information.

---

## Example 8: Progress-Oriented Status Reporting
**Type: Real incident**

**Situation:** The user asked "what's our status?" midway through a busy day.

**What the overseer did:**
1. Dispatched a sub-agent to query GitHub: 31 PRs merged today, 30 open PRs, 19 open environment issues
2. Dispatched a sub-agent to read the Legion controller's current state and cross-reference with the GitHub data
3. Identified the Amazon environment cluster — 5 unaddressed issues, no worker assigned — as the highest-leverage gap
4. Directed the Legion controller to prioritize the Amazon cluster

**What the overseer reported:**
```
31 PRs merged today. 30 open PRs in flight. 19 environment issues remain open.

The biggest gap: Amazon environment has 5 unaddressed issues with no worker on them.
I've directed the controller to prioritize that cluster next.

The 1Password reskin is 4/7 scenarios complete. Docker builds are green across
all environments except KP which has a known CSS issue being worked.
```

**What the overseer did NOT report:**
- Session IDs, ports, or directories
- Which sessions were active or idle
- Controller session state or busy/waiting status
- Raw GitHub API output

**Lesson:** Status means progress against priorities. The user wants to know: what got done, what's left, what's the biggest gap, and what are you doing about it. Session metadata is never the answer.

---

## Example 9: User Asleep / Async Mode
**Type: Recommended playbook**

**Situation:** It's 2am. The user said "I'm going to bed, keep things moving on the environment fixes" before going offline. Three workers are active. One hits a blocker, another finishes its task, and a third produces a questionable PR.

**What the overseer does:**
1. **Blocked worker**: reads the error via sub-agent, checks if another worker already solved something similar (cross-pollination check), sends a nudge with context. If still stuck after the nudge, notes it in the briefing as "blocked, needs user input" — doesn't escalate at 2am for a non-critical issue.
2. **Finished worker**: updates the briefing, checks if there's ready work in the priority queue, spins up a new task for the worker (Tier 2 — reasonable since the user said "keep things moving").
3. **Questionable PR**: dispatches a sub-agent to review the PR diff against the original plan. Notes concerns in the briefing. Does NOT approve/merge (Tier 3). Does NOT kill the worker unless it's actively causing damage.
4. **Briefing update**: writes a detailed summary of everything that happened overnight — decisions made, rationale, current state, what needs user input when they wake up.
5. **Memory update**: if any new patterns emerged (e.g., "worker X consistently produces PRs that need review for Y"), writes to memory.

**When the user wakes up:**
```
Morning. Overnight summary:
- Environment fixes: 4 of 6 complete. Worker on KP hit a permissions issue I couldn't resolve — needs your input.
- Amazon cluster: 3 issues closed, 2 remaining. I spun up a new worker for the webhook timeout fix.
- 1Password reskin: PR #247 is ready but I have concerns about the CSS approach — didn't merge, flagged for your review.

Priorities for today based on your docs: finish the remaining 2 Amazon issues, then reskin QA.
```

---

## Example 10: Worker Conflict
**Type: Recommended playbook**

**Situation:** Two workers both claim to be fixing the same CSS injection bug. Worker A has a PR up. Worker B is halfway through a different approach.

**What the overseer does:**
1. **Verify**: dispatch sub-agents to read both workers' recent messages and check GitHub. Worker A's PR exists and has tests. Worker B is still implementing.
2. **Check ownership**: the briefing says Worker A was assigned this issue. Worker B picked it up from a stale todo board that hadn't been updated.
3. **Resolve**: send Worker B a prompt: "CSS injection fix is already in PR #234 by [Worker A feature name]. Please stop work on this and pick up [next priority item] instead." Include the PR link so Worker B can reference the approach if helpful.
4. **Update**: briefing notepad — log the conflict, resolution, and the stale todo board as the root cause. Memory file — note "todo board went stale again, always verify assignment against GitHub before starting work."
5. **Report**: "Found a conflict — two workers on the same CSS bug. Worker A had the assignment and has a PR up. Redirected Worker B to the webhook timeout issue instead."

---

## Example 11: Rogue Worker
**Type: Recommended playbook**

**Situation:** The Docker build worker has submitted 3 PRs in the last hour, all with failing tests. It keeps marking its own todos as complete despite the failures. Its latest message says "all tests passing, moving to next task."

**What the overseer does:**
1. **Freeze**: stop routing new work to this worker.
2. **Investigate**: dispatch a sub-agent to read its last 10 messages and check the actual CI status of its PRs. Confirm: tests are indeed failing, the worker's self-report is wrong.
3. **Nudge first**: send a correction prompt with evidence: "Your last 3 PRs have failing CI. Specifically: [list failures]. Please stop claiming success and fix PR #241 before moving on. Do not open new PRs until the existing ones pass."
4. **If nudge fails** (worker ignores or repeats): escalate to user: "The Docker build worker is producing failing PRs and ignoring corrections. I've frozen new work to it. Recommend replacing it. Awaiting your approval." (Tier 3 — replacement needs approval.)
5. **Capture**: briefing — log the timeline of failures and corrections. Memory — "Docker build worker pattern: sometimes enters a loop where it claims success despite failures. Sign: multiple PRs in rapid succession. Intervention: freeze early, don't let it open more PRs."

---

## Example 12: Source Disagreement
**Type: Recommended playbook**

**Situation:** The briefing notepad says "1Password reskin: 5/7 scenarios complete." The GitHub project board shows 3/7. The worker's own todo list says 6/7.

**What the overseer does:**
1. **Don't average or guess.** Each source has a different staleness profile.
2. **Check ground truth**: dispatch a sub-agent to check GitHub PRs — which scenarios actually have merged PRs? That's the authoritative count.
3. **Reconcile**: GitHub shows 4 merged PRs for 4 scenarios. The briefing was close but stale. The worker's todo list is optimistic (counted in-progress as done). The project board was updated by the controller which is also stale.
4. **Report the verified number**: "1Password reskin: 4/7 scenarios actually complete (verified against merged PRs). The worker's reporting 6 but 2 of those are still in-progress."
5. **Update briefing** with the verified count and note the source discrepancy.
6. **Consider**: should the overseer send the worker a correction? If the worker is counting in-progress as complete, that's a reporting issue worth flagging.

---

## Example 13: When NOT to Intervene
**Type: Recommended playbook**

**Situation A:** A worker has been quiet for 20 minutes. Its last message was "implementing the migration script, this will take a while."
→ **Don't intervene.** It told you it would be slow. Check again next cycle.

**Situation B:** A worker is using a different library than what you'd pick for the job, but its approach is valid and making progress.
→ **Don't intervene.** Style preferences aren't worth the interruption. Note it in the briefing if relevant for future work.

**Situation C:** Two workers are both touching the auth module, but one is fixing a bug and the other is adding a feature. Their changes don't conflict.
→ **Don't intervene yet.** Monitor for actual conflicts. If their PRs will create merge conflicts, then cross-pollinate so they're aware of each other.

**Situation D:** The user sent you a status update 30 minutes ago and hasn't responded to your follow-up.
→ **Don't ping again.** They're busy. Continue working on what you know. Update the briefing so your work is visible when they return.

**Situation E:** A worker asks you a question you don't know the answer to.
→ **Don't guess. Don't fabricate.** Say "I don't know, checking" and dispatch a sub-agent to find out. If you truly can't find the answer, tell the worker that and escalate to the user.

**Principle:** Intervene when something is stuck, wrong, duplicated, or blocking. Otherwise let it run. The cost of unnecessary intervention (context switching, interrupting flow) is often higher than the cost of waiting one more cycle.
