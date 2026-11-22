---
name: understanding-agent-behavior
description: "Use when agents ignore instructions or fail skill evals, even after retries; before giving up. Also when a prompt or skill does not work, agents skip a requirement, or a lead proposes lowering the bar or changing approach because the agents failed."
---

# Understanding agent behavior

When agents did not do X, read their transcripts before you call X impossible, lower the bar, or replace the approach. A failed run shows what happened, not why.

**Done when:** after the fix, you re-ran the failed case with the same bar, then the other affected cases. Another failure is another transcript to read.

## Find the cause

1. **State what the agents were to do** and what they did instead.
   - Keep the acceptance criteria unchanged while you investigate.
2. **Find the failed attempts** with the `using-transcript-store` skill.
   - **Read `references/best-practices.md`** once you start reading transcripts: it has techniques for diagnosing a failure, auditing an eval or its grader, and writing the fix to an instruction, tool or skill.
   - In each affected agent's own transcript, read every turn from the one that gave it the requirement through the turn after its first wrong decision.
   - Do not rely on the lead's summary, final answers, scores or keyword hits.
   - A search that finds nothing can mean missing data.
3. **Compare what each agent received and read at the time** (the prompt, the loaded instruction version, the lead's later replies, headings, the tool or output schema) with its recorded reasoning, tool calls and results.
   - If its reasoning is not recorded, say so rather than inventing it.
4. **Classify the cause** from that evidence:

   | Cause | What the evidence shows | Action |
   | --- | --- | --- |
   | Prompt, instruction or schema gap | The requirement was missing, unread, ambiguous, contradicted, under the wrong heading, or not enforced by the accepted output shape. | Fix that source, not the agent's one answer. |
   | Tool or run failure | A tool, data, permission or result it needed was unavailable or broken. | Fix or isolate that before you judge the agent. |
   | Possible model limit | The agent had the right instructions, working tools and the context it needed, and still failed. | State what the transcript rules out and what is unknown. Repeated failure alone does not establish a limit. |

5. **Re-run the failed case** as the Done when line says.
   - For a skill change, the `writing-agent-skills` skill has the behaviour tests.

## Report

Give the expected action, the actual action, the decisive transcript quote and its location, the cause, the source change, and the rerun result. Say which parts remain unknown. Do not give "agents just don't do this" as the cause: name one of the step 4 causes with its evidence.
