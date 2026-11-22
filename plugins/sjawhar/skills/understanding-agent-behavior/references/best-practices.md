# Techniques for diagnosing and fixing agent failures

Techniques from published guides, for use once you are reading transcripts.

## Diagnosing

- Reproduce with the simplest case that still fails: if a single-turn version fails too, the cause is retrieval or knowledge, not the conversation.
- Annotate only the first failure in a trace, and fix that one; later errors usually cascade from it, and a taxonomy of symptoms counts one bug many times.
- To find which hand-off breaks, build a transition-failure matrix: rows are the last successful state, columns where the first failure landed.
- Re-run the agent with its exact prompt and tools and watch each step. It shows what a score hides: going on after it had enough results, over-long queries, the wrong tool.
- Give the model its own prompt and the observed failure and ask it to diagnose; it can rewrite a flawed tool description for later agents.
- Read the raw tool calls and results against the stated reasoning: what the agent leaves out matters more than what it says.
- Ask whether the agent could have obtained the information at all. No prompt fixes a web search for something that exists only in Slack.
- When an agent "ignores" a rule, check the tools before the wording: overlapping tools, cryptic ids and opaque errors cause it.
- When an agent skipped a skill's body, read the skill's description: an agent that finds steps there follows them instead of the body (`writing-agent-skills`).
- Do error analysis bottom up: open notes on real traces, then cluster them into categories, then count. Write the rubric after reading traces, not before.
- Record problems that are not the model's fault (a missing feature, a broken tool, bad data) as notes during review. Find their root cause only after you have clustered and counted all the notes (the bullet above), starting with the category that has the most notes: notes over more traces give a better diagnosis.
- Change the model, framework or architecture only after error analysis says the model is the problem.

## Evals and graders

- Before you call it a model limit, suspect the task and the grader: a frontier model at 0% over many trials almost always means a broken task.
- A good task gets the same verdict from two domain experts and states everything the grader checks. Grade the outcome and end state, not the path.
- Do not trust a score, up or down, before someone reads transcripts from that run.
- Test both directions of a behaviour: when it should fire and when it should not.
- Isolate every trial in a clean environment; leftover files, caches and shared state correlate failures.
- Start with 20–50 cases from real failures; early effects are large enough to see on a few cases.
- An eval suite at a 100% pass rate carries no information: retire it or make it harder. This applies to a standing suite, not to the rerun that confirms a fix, which keeps its bar (SKILL.md step 5).
- Skip generic metrics (helpfulness, coherence); they pull attention from the few failures that matter.
- Use a structured rubric with one judge per dimension and an explicit "Unknown" option, so the judge does not invent a verdict.

## Writing the fix

For a guide or a skill, the limits are in the `writing-agent-skills` skill.

- Keep instructions short. Every line of an always-loaded file costs attention on every task, and recall falls as the context fills: hold only what the agent cannot work out from the code.
- Write at the level a capable new hire can apply: heuristics, not if-else logic and not vague principles. Three to five varied examples beat a growing list of edge cases.
- Give a reason only where the rule would look arbitrary, in one clause, not just the rule ("a TTS engine reads this aloud, so no ellipses"): the model generalizes from the reason to cases the rule never named.
- Do not add force with capitals or "CRITICAL": newer models over-trigger on it, so write "Use this tool when ...". A rule that needs force belongs in a hook or a check.
- At a model upgrade, remove anti-laziness and "verify your work" prompting rather than rewording it.
- Do not answer each new failure with another edge case in the prompt: fixing one mode surfaces the next while the prompt grows.
- Put effort-scaling rules in the instruction (this class of task: one agent and 3–10 tool calls; that class: 10 or more subagents); agents judge effort badly.
- Write truncation and error messages as instructions, with a correct example: the agent reads them as its next prompt.
- Fix tool overlap, not tool count: if an engineer cannot say which tool applies, neither can the agent.

## A finer cause taxonomy

If the skill's three causes are too coarse, AgentRx's nine categories are a finer layer: plan adherence failure, invention of new information, invalid invocation, misinterpretation of tool output, intent–plan misalignment, under-specified user intent, intent not supported, guardrails triggered, system failure.
