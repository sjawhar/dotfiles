---
name: reflect
description: "Analyze recent Claude Code, OpenCode, and Oh My Pi sessions and Sami's Dispatch answers for recurring corrections, preferences, and automation opportunities, and measure whether earlier fixes worked. Use for a retrospective over recent agent work."
---

# Reflect

A reflection over the window (default: 7 days) reads everything Sami said, measures how
often each kind of agent failure happened, finds why the top ones happen, and fixes them in
the cheapest place. The newest prior report in `~/.dotfiles/.claude/session-analysis/` is
the baseline: its failure rates and its table of landed changes are what this run compares
against.

## 1. Read Sami's own words, yourself

Sami's words live in two places: his session turns, and his Dispatch comments and ask
answers (agents put decisions to him there). The orchestrating agent MUST read every one
personally, end to end, in time order. Subagents may pre-filter noise mechanically; a
summary of Sami's words is not Sami's words.

```bash
SKILL_DIR=~/.dotfiles/plugins/sjawhar/skills/reflect
python3 "$SKILL_DIR/index-sessions.py" --days <N>   # the session index the extractors read
python3 "$SKILL_DIR/extract-user-messages.py" --days <N> --out prompts.jsonl --text prompts.txt
python3 "$SKILL_DIR/extract-dispatch-human.py" --since <ISO start> --out dispatch.jsonl --text dispatch.txt
python3 "$SKILL_DIR/extract-tool-errors.py" --days <N> --summary   # failed tool calls, top stems
```

When the fleet runs on more than one devbox, copy each other box's
`~/.omp/agent/sessions` files for the window into a scratch directory laid out as a home
(`<scratch>/.omp/agent/sessions`) and run the same helpers with `HOME=<scratch>`. Sessions
moved between hosts carry their history, so dedupe by text.

`extract-user-messages.py` drops injected noise and dedupes exact repeats, but scheduled
runner prompts, probes and agent-written child briefs still get through; mark them as you
read. Redact secrets: Sami pastes tokens, cookies and OAuth codes into answers.

The highest-value learnings are the TURNING POINTS: the small fraction of his messages
that demonstrably changed an architecture or implementation direction, or unstuck an
agent that was spinning. For each one, pull the surrounding session or issue context and
answer: what was the agent doing or stuck on, what did Sami say (verbatim), what changed
after, and what would have let the agent get there without him. Those write-ups are the
core deliverable; rates and taxonomies support them.

## 2. Measure a failure rate per class

A daily job (`daily-measure.py`, armed by `installers/reflect-daily.sh` as
`omp/reflect-daily.service`/`.timer` on sami-agents) already extends two label corpora
one day at a time into `~/.dotfiles/.claude/reflect-store.db` (`reflect-store.py`'s
schema): Sami's Dispatch events with `classify-sami-events.py`'s codebook, and session
turns with `jev-turn-label.py`/the strong model's correction/other/not_sami labels.
Read the store instead of recomputing it:

```bash
python3 "$SKILL_DIR/daily-readout.py"   # every class's daily rate series, next to the
                                         # prior report's "Landed this run" table
```

If the store's newest day is older than this run's window end (the daily timer missed
a day, or this is the first run on a new box), catch it up by hand before reading:
`python3 daily-measure.py run` (needs `secret-run GEMINI_API_KEY TYPESAFE_AI_API_KEY --`;
its module docstring has the credential path and why those two keys). Report per-label
rates by day and by week beside the prior report's, same as before. Before trusting a
label, read 20 random events it carries and state the precision you found. A single
event cannot show that it repeats an earlier ask, so `already_answered` undercounts;
check it against your own reading. Your step-1 reading is the gold set: a failure class
you found by reading that the codebook lacks goes into the codebook
(`classify-sami-events.py`'s `CODEBOOK`, which `daily-measure.py` imports unchanged),
and its rate starts from that run — relabel nothing retroactively; the series simply
gains a new class from here.

**Monthly calibration.** About once a month, read a fresh random sample of ~30 labelled
events/turns the way step 1 and the paragraph above already do, and state the precision
found. The daily job runs on a model's own judgment with nobody reading its output
between weekly sittings; a monthly calibration sample is what catches the labels
drifting from what Sami actually counts as a mistake before a month of rates have
quietly gone stale.

## 3. Find why each top failure happens

`extract-agent-narrative.py` renders every omp transcript in the window (top-level
sessions and every subagent sidecar) as a readable agent-only narrative and writes one
digest row per file. Population is the population: run it over the whole window, then read
narratives chosen from the digest. Its step-back and retry markers are mostly false
positives; choose by tool errors, subagent failures and the sessions behind Sami's
corrections (Dispatch events name the asking session), and verify by reading.

```bash
python3 "$SKILL_DIR/extract-agent-narrative.py" --days <N> --out narratives/   # + digest.jsonl
```

For each top failure class, trace 2-4 instances to the transcript where the agent made the
mistake, check what it had loaded, and decide which applies:

- **Rule-induced:** an instruction it loaded caused the behavior (quote file:line and when
  the text landed).
- **Not discovered:** the right text exists but was not loaded or found where the action
  happened.
- **Ignored:** loaded and clear, violated anyway.
- **Missing:** nothing says it.
- **Capability:** the tool or access is genuinely absent.

## 4. Fix in the cheapest place

In order: delete or correct the text that caused it; make the right text discoverable
where the action happens (a pointer from the skill or tool the agent actually opens); a
mechanical gate (a tool refusal, a lint, a default); a new rule last, and CLAUDE.md last of
all. Rule text states the rule: no incident narratives, no dates or Sami's quotes as
justification, no absolute always/never that agents will apply pedantically. Tighten the
existing text rather than adding a paragraph.

A change to harness behavior ships behind a gate in the omp experiments extension
(`omp/extensions/experiments/gates.json`), so its effect is randomized per session. Every run
reads the gates with `experiments-readout.py`: each randomized feature on versus off, per
session, on the share of Sami's turns that correct the agent, the share of merged PRs marked
rework, and model spend per merged PR, each with a 95% interval. Turn labelling is daily now
(step 2): dump the store's turn_labels with
`python3 "$SKILL_DIR/reflect-store.py" dump-turn-labels --out turn-labels.jsonl` (the
id shape already matches `experiments-readout.py`'s own cache file, so this is a
drop-in `turn_labels_path`/`cache_path`), then load `experiments-readout.py` in an eval
cell, pass both boxes' session directories, step 1's prompt files, and that dump as
`turn_labels_path`; only turns outside the daily job's window (if any) still escalate
live through `label_turns`'s own Jev-first, strong-model-escalated path (confidence >=
0.5; measured 91.2% agreement with the strong model on the 91.6% of turns it answers
confidently, escalation explicit, never silent -- `jev-turn-label.py`'s docstring has
the exact commands and the full measurement is in
`.claude/session-analysis/2026-10-04-work/jev-evaluation.md`). Report every feature's
three intervals; there is no stop rule. A week of traffic detects only large effects,
so a feature whose interval includes 0 stays random rather than being called a wash.

A text change lands for everyone and is read from the step-2 rates in later runs; changes
that land within a day of each other cannot be told apart, so say so instead of attributing.
Every report ends with a table of what landed in this run (commit or PR, timestamp, the
failure class it targets): the next run's before/after.

## Lanes and synthesis

Run these in parallel; each gets the step-1 corpora, the narratives, and the prior report:

- **Mistake Finder:** step 3 for the top classes.
- **Preference Learner:** recurring preferences; for each, where it is written and whether
  it was violated after it was written.
- **Command Repeater:** failed tool calls and repeated command sequences, fixed
  mechanically where possible.
- **Prompt Repeater:** repeated session-opening requests and repeated Dispatch ask
  templates.
- **CLAUDE.md Miner:** always-loaded text audited against the step-4 rule-writing
  constraints; the target is net shrinkage.
- **Memory Groomer:** long-term memory cleanup (procedure below).

Model floor: no smol/cheap-tier models anywhere judgment is involved — readers,
classifiers, verifiers, synthesis, grooming. Fast models are only for mechanical inventory
(counting, globbing, symlink maps). A cheap reader produces confident shallow output that
poisons every downstream step.

Rank patterns by frequency and impact. Assess each earlier improvement as improved,
unchanged or regressed from the rates and the prior landed table. Put approved skills in
`plugins/sjawhar/skills/`, agents in `plugins/sjawhar/agents/`, commands in
`plugins/sjawhar/commands/`, and global rules in `.claude/CLAUDE.md`. Distill the period
into 3–5 durable summary facts (recurring corrections, changed decisions) and store them
with `retain`.

## Presenting findings and decisions

Never end the retro by dumping a numbered menu of every open decision. That format is
itself one of the failure patterns this skill exists to catch (buried context, zero
explanation, cognitive offload onto Sami).

- **At most 3 decisions per message**, each as current state → what changed / what the
  evidence says → concrete options with tradeoffs → your recommendation. If more are
  pending, present the top ones and say what's queued; bring the rest in later messages
  as the earlier ones resolve.
- **Every decision gets its explanation inline.** A one-line label plus a section
  reference ("§D, 9 items") is not a presentable decision. If a decision needs the
  report open in another window to understand, it isn't ready to present.
- **Rule proposals are reviewed one at a time, not as a batch.** Say for each why the
  cheaper homes in step 4 don't fit.
- **Plain language throughout**: no counts-as-argument ("57 rows say…") without one
  concrete example, no internal shorthand from the analysis (reader names, category
  labels) unless defined in the same sentence.

## Memory grooming

The Memory Groomer cleans long-term memory instead of reading sessions. Banks are
SQLite under `~/.omp/agent/memories/mnemopi/` (`mnemopi.db` global, `banks/*/mnemopi.db`
per project); `memory_edit` reaches only the active scope (global + current project) —
inventory other banks read-only and flag them for grooming from their own project.

1. Inventory candidates via read-only SQLite reads (`working_memory`, `facts`,
   low-veracity rows). Never write SQL; mutate only through `memory_edit`.
2. `recall` sweeps: topics from the window's corrections, plus user preferences, project
   decisions, and tooling facts.
3. Near-duplicates: keep the best-worded memory; `invalidate` the rest with
   `replacement_id` pointing at the survivor.
4. Contradictions: decide current truth by recency and transcript evidence; `update`
   the survivor (read the full `memory://<id>` first — previews truncate) and
   `invalidate` the losers.
5. A ruling Sami gave for one context, stored as a standing rule, gets re-scoped with
   `update` or invalidated; point-in-time status (a PR "merge-ready", a pin, "blocked
   on X") that is no longer true gets invalidated.
6. `forget` only pure noise with zero historical value; otherwise `invalidate`.
7. Return a ledger: every id, operation, one-line reason.

The first grooming run is propose-only: if no prior report has a "Memory grooming"
section, apply nothing and put the full proposed ledger in the report for review.
Later runs apply directly, except a run proposing more than 20 mutations presents
the list and waits for approval.

Save reports as `YYYY-MM-DD.md` in `~/.dotfiles/.claude/session-analysis/`, with the
grooming ledger under a "Memory grooming" heading. If an agent fails, continue and record
it. End by suggesting the user run `/memory enqueue` to consolidate the groomed state.
