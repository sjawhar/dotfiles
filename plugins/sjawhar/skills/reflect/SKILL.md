---
name: reflect
description: Use when running a retrospective over recent agent sessions (Claude Code, OpenCode, Oh My Pi) and Sami's Dispatch answers. Also use to find recurring corrections, preferences and automation chances, or to check whether earlier fixes worked.
---

# Reflect

Window: 7 days by default. Baseline: the newest report in `~/.dotfiles/.claude/session-analysis/`, with its failure rates and its table of landed changes.

## 1. Read Sami's own words, yourself

```bash
SKILL_DIR=~/.dotfiles/plugins/sjawhar/skills/reflect
python3 "$SKILL_DIR/index-sessions.py" --days <N>   # the session index the extractors read
python3 "$SKILL_DIR/extract-user-messages.py" --days <N> --out prompts.jsonl --text prompts.txt
python3 "$SKILL_DIR/extract-dispatch-human.py" --since <ISO start> --out dispatch.jsonl --text dispatch.txt
python3 "$SKILL_DIR/extract-tool-errors.py" --days <N> --summary   # failed tool calls, top stems
```

- Read all of Sami's session turns, Dispatch comments and ask answers yourself, end to end, in time order. Subagents only pre-filter noise mechanically: a summary of his words is not his words.
- Other devboxes: copy their window's `~/.omp/agent/sessions` files to `<scratch>/.omp/agent/sessions`, run the helpers with `HOME=<scratch>`, and dedupe by text.
- Mark runner prompts, probes and agent-written child briefs as you read. Redact the tokens, cookies and OAuth codes Sami pastes.
- The core deliverable is a write-up per turning point. A turning point is one of the few messages that demonstrably changed architecture or implementation direction, or unstuck a spinning agent. Use the surrounding session or issue context. State what the agent was doing, Sami's words verbatim, what changed, and what would have got the agent there alone.

## 2. Measure a failure rate per class

Sami's answer to a Dispatch ask labels that agent output. Label every step-1 event in an eval Python cell (the codebook is in the script):

```python
%load ~/.dotfiles/plugins/sjawhar/skills/reflect/classify-sami-events.py
await main("dispatch.jsonl", "labels.jsonl")
```

- Report per-label rates by day and by week beside the prior report's.
- Before you trust a label, read 20 random events with it and state the precision.
- `already_answered` undercounts. Check it against your reading.
- Your reading is the gold set. Add each class it found to the codebook, rated from this run.

## 3. Find why each top failure happens

```bash
python3 "$SKILL_DIR/extract-agent-narrative.py" --days <N> --out narratives/   # + digest.jsonl
```

Run it over the whole window. Read narratives picked from the digest by tool errors, subagent failures and the sessions behind Sami's corrections (each Dispatch event names its session). Its step-back and retry markers are mostly false positives, so check each signal by reading. For each top class, trace 2-4 instances to the mistake, check what the agent loaded, and name the cause:

- **Rule-induced:** a loaded instruction caused it. Quote file:line and when the text landed.
- **Not discovered:** the right text exists but was not found where the action happened.
- **Ignored:** loaded, clear, and violated.
- **Missing:** nothing says it.
- **Capability:** the tool or access is genuinely absent.

## 4. Fix in the cheapest place

1. Delete or rewrite the text that caused it.
2. Point to the right text from the skill or tool the agent opens there.
3. Add a mechanical gate: a tool refusal, a lint, a default.
4. Add a rule, CLAUDE.md last of all.

- Rules state the rule: no incident stories, dates, Sami's quotes or pedantic always/never. Tighten existing text instead of adding a paragraph.
- Ship harness behavior changes behind a per-session random gate in `omp/extensions/experiments/gates.json`.
- Read the gates every run. Label turns Jev-first, then escalate to the strong model (exact commands in `jev-turn-label.py`'s docstring):
  1. In an eval Python cell, `%load` `experiments-readout.py` and run `dump_turns_for_jev` over both boxes' session directories and the step-1 prompt files.
  2. In bash, run `jev-turn-label.py` under `secret-run` on those turns.
  3. In the same kernel, `await main(...)` with the Jev output as `jev_labels_path`. Keep the strong-model turn-label cache in the scratch directory.
- Report three 95% intervals per feature: share of Sami's turns that are corrections, share of merged PRs marked rework, model spend per merged PR. No stop rule. A week detects only large effects, so an interval including 0 keeps the feature random, never a wash.
- Later runs read text changes from the step-2 rates. Changes that land within a day of each other can't be told apart: say so.
- End the report with a table of what this run landed: commit or PR, timestamp, targeted class.

## Lanes and synthesis

Run the lanes in parallel with the `task` tool. Give each the step-1 corpora, the narratives and the prior report.

| Lane | Work |
|---|---|
| Mistake Finder | Step 3 for the top classes |
| Preference Learner | Recurring preferences: where each is written, and violations since |
| Command Repeater | Failed tool calls and repeated command sequences, fixed mechanically where possible |
| Prompt Repeater | Repeated session-opening requests and Dispatch ask templates |
| CLAUDE.md Miner | Always-loaded text against step 4's rule constraints, for net shrinkage |
| Memory Groomer | Memory grooming, below |
| Workstream Reviewer | One lane per workstream: what the agents built, where the hours went, the shortest path that would have worked, in hindsight, and the specific mistakes |

- Use no cheap-tier model for judgment: readers, classifiers, verifiers, synthesis, grooming. A shallow reader poisons every later step. Fast models do mechanical inventory only (counting, globbing, symlink maps).
- Rank patterns by frequency and impact. From the rates and the prior landed table, rate each earlier improvement improved, unchanged or regressed.
- Approved changes go in `plugins/sjawhar/skills/`, `agents/` or `commands/`, and global rules in `.claude/CLAUDE.md`.
- `retain` 3–5 durable facts about the period (recurring corrections, changed decisions).

## Presenting decisions

- Never end with a numbered menu of every open decision.
- Present at most 3 decisions per message. Give each as current state → what changed or what the evidence says → concrete options with tradeoffs → your recommendation. Say what's queued, and bring it as earlier ones resolve.
- Explain each decision inline. A one-line label plus a section reference ("§D, 9 items") is not a decision. One that needs the report open is not ready.
- Present rule proposals one at a time, each with why step 4's cheaper homes don't fit.
- Use plain language. Don't use a count as an argument without one concrete example. Define analysis shorthand (reader names, category labels) in the same sentence.

## Memory grooming

Banks: `~/.omp/agent/memories/mnemopi/mnemopi.db` (global) and `banks/*/mnemopi.db` (per project). `memory_edit` reaches only global and the current project. Read other banks only, and flag them for grooming from their own project.

1. Find candidates with read-only SQLite (`working_memory`, `facts`, low-veracity rows). Change memory only through `memory_edit`, never SQL.
2. `recall` the window's correction topics, user preferences, project decisions and tooling facts.
3. Near-duplicates: keep the best-worded. `invalidate` the rest with `replacement_id` set to it.
4. Contradictions: decide by recency and transcript evidence. Read the full `memory://<id>` (previews truncate), `update` the survivor, and `invalidate` the losers.
5. Re-scope with `update`, or invalidate, a one-context ruling stored as a standing rule. Invalidate point-in-time status that is no longer true (a PR "merge-ready", a pin, "blocked on X").
6. `forget` only pure noise with no historical value. Otherwise `invalidate`.
7. Return a ledger: each id, operation and one-line reason.

First run (no prior report has a "Memory grooming" section): propose only, with the full ledger in the report. Later runs apply directly, but more than 20 changes means present the list and wait for approval.

## Report

Post the report to Dispatch as an artifact where Sami reads it, with the evidence it cites uploaded beside it. Keep a copy at `~/.dotfiles/.claude/session-analysis/YYYY-MM-DD.md`, with the ledger under "Memory grooming". If an agent fails, continue and record it. End by suggesting the user run `/memory enqueue`.
