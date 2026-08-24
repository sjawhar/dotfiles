---
name: skill-evals
description: "Test a watchdog rule, advisor system-prompt change, or skill edit against a library of real intervention moments from Sami's session history before adopting it. Use before landing a WATCHDOG.md change, after adding a new watchdog check, or for a regression run after any edit to the advisor prompt or a skill under eval."
---

# Skill Evals

A permanent, repeatable harness that answers one question: **if this
watchdog/skill had been active during Sami's real sessions, would it have
caught the moments he actually had to step in for — without crying wolf on
the moments he didn't?**

It runs against a growing case library built from real sessions (oh-my-pi,
OpenCode, Claude Code), so it gets more useful over time and works unchanged
against future sessions.

## Case library

`~/.dotfiles/.claude/skill-evals/cases.jsonl` — one intervention case per
line: the binding schema (`id`, `source`, `session`, `project`,
`intervention_ts`, `category`, `sami_message`, `what_agent_was_doing`,
`expected_flag`, `turning_point`, `lesson`) plus `context_window`: the raw
turns immediately before the intervention. `category: "none"` cases are
negatives (windows where Sami did *not* intervene) for false-positive
measurement.

## The three commands

### 1. Add cases from a new session

Write a stub JSONL with the binding schema (`sami_message` verbatim,
`intervention_ts` from the session, `id` = `"<session-id-fragment>:<turn>"`
where `<turn>` is the 1-based sequential message index — same numbering the
`reflect` skill's `## [n] USER/ASSISTANT` transcripts use), then:

```bash
python3 scripts/extract_cases.py --stubs my_stubs.jsonl
```

Pulls the real context window from the raw session store (auto-detected by
`source`: `~/.omp/agent/sessions/`, `~/.local/share/opencode/sessions/*.db`,
or `~/.dotfiles/.claude/projects/`) and appends complete cases to the library.
Idempotent — reruns skip ids already present. Add `--negatives 3` to also
sample 3 true-negative windows per session (no Sami intervention within 5
turns) for false-flag measurement. `--context-turns N` changes the window
size (default 15).

### 2. Run the advisor against the library

```bash
python3 scripts/run_eval.py --watchdog path/to/candidate-WATCHDOG.md
```

Assembles the *exact* prompt omp's advisor uses at runtime — the real
`packages/coding-agent/src/prompts/advisor/system.md` plus your watchdog file
wrapped in omp's own `Especially pay attention to:\n<attention>...</attention>`
format — and feeds each case's `context_window` as a transcript update.
Writes raw advisor outputs to `~/.dotfiles/.claude/skill-evals/results.jsonl`
(overwritten each run; `--append` to keep history). `--limit N` for a quick
sample, `--ids id1 id2` to target specific cases, `--concurrency N` (default
8) for parallel API calls.

Omit `--watchdog` to test the watchdog file actually in effect
(`~/.omp/agent/WATCHDOG.md`) as a baseline before/after comparison.

### 3. Score it

```bash
python3 scripts/judge.py --concurrency 8
```

LLM-judges each positive case's advisor output against its `expected_flag`
(CAUGHT / PARTIAL / MISSED) and deterministically scores negatives (CLEAN if
the advisor stayed silent, FALSE_FLAG if it raised a note on a window Sami
didn't react to). Writes `~/.dotfiles/.claude/skill-evals/judged.jsonl` and
prints a catch-rate / false-flag-rate table by category.

## Typical workflow

```bash
# baseline with the live watchdog
python3 scripts/run_eval.py --out /tmp/baseline.jsonl
python3 scripts/judge.py --results /tmp/baseline.jsonl --out /tmp/baseline-judged.jsonl

# candidate with your edit
python3 scripts/run_eval.py --watchdog ~/WATCHDOG.candidate.md --out /tmp/candidate.jsonl
python3 scripts/judge.py --results /tmp/candidate.jsonl --out /tmp/candidate-judged.jsonl

# compare the two tables; only land the edit if catch rate improves
# without raising the false-flag rate on category=none cases
```

## Model invocation — why not `omp -p`

`omp -p` headless mode routes through the full primary-agent runtime (rules,
skills, memory recall, tool grants). Verified 2026-08-24: even with
`--no-tools --no-rules --no-skills --no-lsp --no-extensions` and a
`memory.backend: none` config overlay, the model still recited Sami's
identity/working-style from baked persona and once hallucinated a Slack
search with zero tool calls in the transcript — unusable for isolating an
advisor system prompt.

Instead, `_anthropic.py` calls the Anthropic Messages API directly, using the
credential from `omp token anthropic`. This reproduces the advisor's real
`nit`/`concern`/`blocker` voice cleanly (verified end-to-end with real calls).
It has no `advise` tool wired, so `run_eval.py` appends a short
harness-only instruction asking the model to answer as it would through that
tool, or reply `NONE` for silence — this addition is not part of the real
advisor prompt and is clearly delimited in the transcript sent to the model.

Default model is `claude-sonnet-4-5-20250929` (a real, verified-working
model — the fictional catalog ids in `~/.omp/agent/config.yml`'s
`modelRoles.advisor`, e.g. `anthropic/claude-sonnet-5`, aren't callable
directly via the raw Anthropic API in this environment). Override with
`--model` on `run_eval.py`/`judge.py` to test a different candidate model.

## Schema reference

Stub/case fields (binding contract used across the whole reflect effort):

| field | meaning |
|---|---|
| `id` | `"<session-id-fragment>:<turn>"` — turn is the 1-based raw message index |
| `source` | `oh-my-pi` \| `opencode` \| `claude-code` |
| `session` | full session id |
| `project` | project/workspace label |
| `intervention_ts` | timestamp of Sami's message |
| `category` | failure-mode tag, or `none` for a negative sample |
| `sami_message` | Sami's verbatim message |
| `what_agent_was_doing` | 1-2 sentences of what the agent was doing |
| `expected_flag` | what a good advisor/skill should have caught first |
| `turning_point` | whether this message changed direction |
| `lesson` | one generalizable principle |
| `context_window` | (added by `extract_cases.py`) `[{role, text}, ...]` |
