---
name: understanding-agent-behavior
description: "Use when agents ignore instructions or fail skill evals, even after retries; before giving up. Also when a prompt or skill does not work, agents skip a requirement, or a lead proposes lowering the bar or changing approach because the agents failed."
---

# Understanding agent behavior

If you asked agents to do X and they didn't, read their actual transcripts before saying X is impossible, lowering the bar, or replacing the approach. A failed run tells you what happened, not why.

Once you are reading transcripts, `references/best-practices.md` collects field-tested techniques and common pitfalls — diagnosing failures, and writing prompts, skills, and evals agents actually follow — drawn from published guides.

## Find the cause

1. State what the agents were supposed to do and what they did instead. Keep the acceptance criteria unchanged while you investigate; a lower bar hides the failure.
2. Find the failed attempts with `skill://using-transcript-store`, then read the actual turns around the first wrong decision. Read the affected agents, not just the lead's summary, final answers, scores, or keyword hits. A missing search result can mean missing data, not missing evidence.
3. Compare what each agent actually received and read at the time with its recorded reasoning, tool calls, and results. Include the prompt, loaded instruction version, lead's later replies, headings, and tool or output schema: these can make the wrong action seem correct. Use reasoning that is recorded; if it is absent, say so rather than inventing it.
4. Classify the cause from that evidence:
   - **Prompt, instruction, or schema gap:** the requirement was missing, unread, ambiguous, contradicted, scoped under the wrong heading, or not enforced by the accepted output shape. Fix that source, not the agent's one answer.
   - **Tool or run failure:** the required tool, data, permission, or result was unavailable or broken. Fix or isolate that failure before judging the agent's ability.
   - **Possible model limit:** the agent had the right instructions, usable tools, and needed context, but still could not do the task. State what the transcript rules out and what remains unknown; repeated failures alone do not establish a limit.
5. Re-run the failed case after the source fix, with the same bar, then check other affected cases. For a skill change, use `skill://writing-agent-skills` for the behavior tests. Another failure is another transcript to diagnose, not permission to declare the task impossible.

## Report the evidence

Give the expected action, the actual action, the decisive transcript quote and location, the cause, the source change, and the observed rerun result. Say which parts remain unknown. Do not substitute “agents just don't do this” for an explanation.
