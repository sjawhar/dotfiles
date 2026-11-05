# Field best practices for diagnosing agent failures

Techniques the field uses once you are already reading transcripts (see SKILL.md). Each bullet comes from a source opened and read; the tag points at the SOURCES list. Nothing here restates the skill's core method (read the transcripts, classify the cause, keep the bar) — these are the next-level techniques.

## Best practices

- Reproduce the failure with the simplest test that still fails before blaming the long context: if a
  single-turn version of the question fails too, the cause is retrieval or knowledge, not the conversation. [S6]
- Annotate only the **first** failure in a trace and fix that; downstream errors usually cascade from it, and
  a taxonomy built on symptoms counts the same bug many times. [S6]
- Build a transition-failure matrix — rows: last successful state, columns: where the first failure landed —
  to find which hand-off actually breaks, instead of guessing from totals. [S6]
- Re-run the agent with its exact prompt and tools and watch it step by step ("think like your agent"); this
  surfaces failures a score never shows: continuing after it had enough results, over-long queries, wrong tool. [S1]
- Hand the model its own prompt plus the observed failure mode and ask it to diagnose: Anthropic's
  tool-testing agent repeatedly used a flawed tool, rewrote the tool description, and cut task completion
  time 40% for later agents. [S1][S3]
- Read the raw tool calls and results against the agent's stated reasoning: what the agent omits matters
  more than what it says, and it does not always say what it means. [S3]
- Put explicit effort-scaling rules in the instruction (this class of task = 1 agent and 3–10 tool calls;
  that class = 10+ subagents). Agents judge appropriate effort badly, and over-investment was a top early
  failure mode. [S1]
- Give the reason behind a rule, not just the rule ("a TTS engine reads this aloud, so never use ellipses"
  beats "NEVER use ellipses"): the model generalizes from the explanation to cases the rule never named. [S5]
- Aim for the "right altitude": heuristics a smart new hire could apply, not hardcoded if-else logic and not
  vague principles; 3–5 diverse canonical examples beat an appended list of edge cases. [S2][S5]
- Before you call it a model limit, suspect the task spec and the grader: with a frontier model a 0% pass
  rate over many trials almost always means a broken task. Opus 4.5 scored 42% on CORE-Bench until rigid
  grading, ambiguous specs and irreproducible tasks were fixed — then 95%. [S4]
- A good task is one where two domain experts independently reach the same pass/fail verdict, and everything
  the grader checks is stated in the task; grade the outcome and end state, not the path taken. [S4]
- Test both directions of a behavior — when it should fire and when it should not. One-sided evals produce
  one-sided agents (test only "did it search?" and you get an agent that searches for everything). [S4]
- Isolate every trial in a clean environment: leftover files, caches and shared state correlate failures and
  inflate scores — Claude was observed reading previous trials' git history for an unfair advantage. [S4]
- Check tool ergonomics before prompt wording when an agent "ignores" a rule: overlapping tools, cryptic
  UUIDs and opaque errors cause it. Resolving UUIDs to human-readable names measurably reduced hallucination
  in retrieval. [S3]
- Write truncation and error messages as instructions ("use the filter parameter", with a correctly formed
  example) — the agent reads them as its next prompt, so an opaque traceback wastes the retry. [S3]
- Do error analysis bottom-up: open-ended notes on real traces, then cluster the notes into a taxonomy, then
  count. At NurtureBoss three categories accounted for >60% of all failures, one of which (date handling)
  went from 33% to 95% once named. [S7]
- Write the rubric *after* reading traces, not before: reviewers given a rubric first check its boxes and
  miss everything outside it, and your own criteria drift as you read. [S6]
- Start with 20–50 cases drawn from real failures, not hundreds: early effect sizes are large enough that a
  prompt change moving 30%→80% is visible on a handful of cases. [S1][S4]

## Things to avoid

- Don't reach for a different model, framework or architecture as the first response to a failure — only
  after error analysis says the model is the problem. [S6][S7]
- Don't trust an eval score, up or down, before someone has read transcripts from that run: an unfair
  grader, an ambiguous task or a constrained harness looks exactly like an incapable agent. [S4]
- Don't celebrate a 100% pass rate: a saturated eval carries no information, and ~70% is usually the more
  informative suite. Retire it or make it harder. [S6][S4]
- Don't add generic metrics (helpfulness, coherence, "hallucination score"); they create a false sense of
  measurement and fragment attention away from the few failures that matter. [S7]
- Don't answer each new failure by appending another edge case to the prompt: that is the whack-a-mole
  plateau — fixing one mode surfaces the next while the prompt grows unwieldy. [S8][S2]
- Don't keep adding tools. The problem is overlap, not count: teams run 15+ distinct tools fine and struggle
  with 10 overlapping ones. If a human engineer can't say which tool applies, the agent can't either. [S10][S2]
- Don't assume more context means more adherence: recall degrades as the window fills (context rot), so
  treat the attention budget as finite and spend it on the smallest high-signal set. [S2]
- Don't carry anti-laziness or "verify your work" prompting across a model upgrade: on newer models the same
  words cause over-triggering and over-verification. Remove them rather than rewording. [S5]
- Don't assert step-by-step tool sequences in graders; agents regularly find valid paths the eval author
  didn't anticipate, and the test then measures the author's imagination. [S4]
- Don't let one LLM judge score many dimensions at once; use a structured rubric with an isolated judge per
  dimension, and give it an explicit "Unknown" escape so it doesn't invent a verdict. [S4]
- Don't skip the question of whether the agent could have obtained the information at all — an agent
  searching the web for something that exists only in Slack is doomed from the start, and no prompt fix
  applies. [S1]
- Don't ignore problems that aren't the model's fault during review (missing feature, broken tool, bad
  data); record them, and defer root-cause until after you have prioritized. Deferring it yields better
  annotations over more data. [S6]

## Optional: a finer cause taxonomy

If the skill's three causes (prompt/instruction/schema gap, tool/run failure, possible model limit) are too coarse, the AgentRx nine-category taxonomy [S9] is a ready-made finer layer: plan adherence failure, invention of new information, invalid invocation, misinterpretation of tool output, intent–plan misalignment, under-specified user intent, intent not supported, guardrails triggered, system failure.

## SOURCES

Every URL below was opened and read in full or in the sections cited.

- [S1] Anthropic, "How we built our multi-agent research system" — https://www.anthropic.com/research/multi-agent-research-system
- [S2] Anthropic, "Effective context engineering for AI agents" — https://www.anthropic.com/research/effective-context-engineering-for-ai-agents
- [S3] Anthropic, "Writing effective tools for agents — with agents" — https://www.anthropic.com/research/writing-tools-for-agents
- [S4] Anthropic, "Demystifying evals for AI agents" — https://www.anthropic.com/research/demystifying-evals-for-ai-agents
- [S5] Anthropic, "Prompting best practices" (Claude docs) — https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- [S6] Hamel Husain & Shreya Shankar, "AI Evals: Everything You Need to Know" (FAQ) — https://hamel.dev/blog/posts/evals-faq/
- [S7] Hamel Husain, "A Field Guide to Rapidly Improving AI Products" — https://hamel.dev/blog/posts/field-guide/
- [S8] Hamel Husain, "Your AI Product Needs Evals" — https://hamel.dev/blog/posts/evals/
- [S9] Microsoft Research, "Systematic debugging for AI agents: Introducing the AgentRx framework" — https://www.microsoft.com/en-us/research/blog/systematic-debugging-for-ai-agents-introducing-the-agentrx-framework/
- [S10] OpenAI, "A practical guide to building agents" (PDF) — https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf

Note on the Hamel sources [S6], [S7], [S8]: all by Hamel Husain and overlapping heavily, so a claim often
appears in more than one; each bullet is tagged with the page where that claim is actually stated. [S6] (the
2026 FAQ) carries the multi-turn debugging, the transition-failure matrix, first-failure annotation, the
rubric-after/criteria-drift point, model-selection-last, the ~70% pass-rate point, and recording problems that
are not the model's fault. [S7] (the field guide) carries bottom-up error analysis, the NurtureBoss figures,
and generic-metrics-as-vanity. [S8] (the 2024 post) carries the whack-a-mole plateau.
