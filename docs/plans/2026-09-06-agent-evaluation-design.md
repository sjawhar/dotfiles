# Agent evaluation suite

**Status:** Approved for implementation

## Purpose

When an agent fails, turn the situation into a reusable test. Compare the current behavior with a proposed change, inspect what improved or regressed, and keep the test for future changes.

The changes we can test are skill descriptions and bodies, agent system prompts, `AGENTS.md`, advisor instructions, and MCP tools or hooks. They can be tested individually or together.

## One suite, built on Inspect

Use Inspect for model calls, agent execution, sandboxing, scoring, logs and viewing results. Keep the code in `evals/` in dotfiles. Use existing skill-evaluation tools as references, not additional platforms we must maintain. Harbor is optional for importing sandbox tasks, not required to run our own.

## Four kinds of test

| Test | What we give the agent | What we measure |
|---|---|---|
| **Skill activation** | A scenario and the competing skill descriptions | Whether the right skill actually loads before it is needed; unnecessary and incorrect loads too. |
| **Instruction effects** | The same task under different skill bodies, agent prompts or `AGENTS.md` content | Whether the change improves behavior. Test skills both already loaded and through normal discovery. |
| **Advisor** | A recorded transcript, limited to what the advisor could see | Whether it should speak, whether its advice is grounded and useful, and false positives/negatives. Separately test whether delivered advice changes the agent's actions. |
| **MCP tools and hooks** | The same task with different tools, feedback or restrictions | Tool selection, input handling, recovery after feedback, task success and bypass attempts. The tool may contain deterministic code or agent logic. |

## Three levels of effect testing

1. **One-shot:** produce the next response or proposed action. Cheap, but does not prove execution.
2. **Simulated rollout:** run the agent while another LLM supplies tool results from a defined environment state. Test decisions and recovery without rebuilding the environment.
3. **Real rollout:** run the actual agent and tools in an isolated sandbox. Check the resulting state, not just the agent's claim of success.

Support all three. Use overlapping scenarios to learn when the cheaper tests predict real behavior and when they don't.

## Making comparisons trustworthy

Freeze the baseline, cases and grading criteria before running candidates. Compare versions on the same cases, changing only the intended intervention. Preserve actual prompt composition and tool/skill availability. Repeat trials and report uncertainty, failures, cost and concrete examples, not just a headline score.

Use code checks where possible and human-calibrated LLM graders for judgment. Harmful advice fails even when it identifies a real problem. Count a false advisor alert as three missed-nit units; report serious misses separately. Advisor interruption and hooks are behaviors to test, not assumed guarantees of prevention.

Rebuild historical cases with tool calls/results intact and review their labels. Missing user corrections do not establish a negative example. Keep expected answers and later corrections out of the agent's view.

## Delivery

Build the four test types in parallel, sharing case formats, scenario fixtures and reporting. Reuse the existing watchdog evaluator's direct-model-call isolation approach within Inspect.

The first complete suite must run every test type, include simulated and real execution, and demonstrate the full loop: capture a failure, compare a change, save the case, then catch a later regression. It evaluates changes; it does not deploy them automatically.

Private cases and logs belong outside public dotfiles in approved, versioned storage. Historical cases require label and privacy review before model use. Set Inspect's log directory explicitly under that private storage. Executable trials and bypass probes use Docker, never Inspect's `local` provider or host-side execution, with fresh state and no real user credentials, services or memory.
