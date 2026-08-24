---
name: reflect
description: "Analyze recent Claude Code, OpenCode, and Oh My Pi sessions for recurring corrections, preferences, and automation opportunities. Use for a retrospective over recent agent work."
---

# Reflect

For the requested number of days (default: 7), index all three sources (Claude Code,
OpenCode, Oh My Pi) and inspect flagged turns without copying session content into
the database:

```bash
INDEXER=~/.dotfiles/plugins/sjawhar/skills/reflect/index-sessions.py
python3 "$INDEXER" --days <N>
SESSION_DB="$(python3 -c 'import runpy,sys; print(runpy.run_path(sys.argv[1])["get_db_path"]())' "$INDEXER")"
sqlite3 "$SESSION_DB" "SELECT s.source,s.project,s.id,t.turn_number,f.flag_type FROM flags f JOIN turns t ON t.id=f.turn_id JOIN sessions s ON s.id=t.session_id WHERE s.timestamp > datetime('now','-<N> days') ORDER BY s.timestamp DESC;"
```

Redact secrets before sharing flagged content. Keep the initial prompt and adjacent
turns; compare with the newest prior report.

Run six agents in parallel; each gets: focus + flagged content + prior-report context.

- **Mistake Finder:** corrected agent errors.
- **Preference Learner:** recurring user preferences.
- **Command Repeater:** workflow candidates.
- **Prompt Repeater:** similar initial requests.
- **CLAUDE.md Miner:** project-specific durable rules.
- **Memory Groomer:** long-term memory cleanup (procedure below).


## The primary analysis: read Sami's own words, yourself

Sami sends few enough messages in any window that the orchestrating agent can and MUST
read every single one personally — extract all genuine user messages (drop injected
notifications, goal-loop re-prompts, slash-command bodies) in chronological order and
read them end to end. Never delegate this reading; subagents may pre-filter noise
mechanically, but a summary of Sami's words is not Sami's words.

The highest-value learnings are the TURNING POINTS: the small fraction of his messages
that demonstrably changed an architecture or implementation direction, or unstuck an
agent that was spinning. For each one, pull the surrounding session context and answer:
what was the agent doing or stuck on, what did Sami say (verbatim), what changed after,
and what would have let the agent get there without him. Those write-ups are the core
deliverable; correction taxonomies and frequency counts are secondary supporting
material, not the product.

Model floor: no smol/cheap-tier models anywhere judgment is involved — readers,
verifiers, synthesis, grooming. Fast models are only for mechanical inventory
(counting, globbing, symlink maps). A cheap reader produces confident shallow output
that poisons every downstream step.

Deduplicate and rank patterns by frequency and impact. Assess earlier improvements as
improved, unchanged, or regressed. Put approved skills in `plugins/sjawhar/skills/`,
agents in `plugins/sjawhar/agents/`, commands in `plugins/sjawhar/commands/`, and
global rules in `.claude/CLAUDE.md`. Distill the period into 3–5 durable summary facts
(recurring corrections, changed decisions) and store them with `retain`.

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
- **Rule proposals are reviewed one at a time, not as a batch**, and CLAUDE.md is not
  the default home — it is the last resort. Prefer, in order: an existing skill's text,
  a new/edited skill, a mechanical gate (CI check, lint, tool default), and only then a
  global rule. Say for each proposal why the cheaper homes don't fit.
- **Plain language throughout**: no counts-as-argument ("57 rows say…") without one
  concrete example, no internal shorthand from the analysis (reader names, category
  labels) unless defined in the same sentence.

The Memory Groomer cleans long-term memory instead of reading sessions. Banks are
SQLite under `~/.omp/agent/memories/mnemopi/` (`mnemopi.db` global, `banks/*/mnemopi.db`
per project); `memory_edit` reaches only the active scope (global + current project) —
inventory other banks read-only and flag them for grooming from their own project.

1. Inventory candidates via read-only SQLite reads (`working_memory`, `facts`,
   low-veracity rows). Never write SQL; mutate only through `memory_edit`.
2. `recall` sweeps: topics from the flagged content, plus user preferences, project
   decisions, and tooling facts.
3. Near-duplicates: keep the best-worded memory; `invalidate` the rest with
   `replacement_id` pointing at the survivor.
4. Contradictions: decide current truth by recency and transcript evidence; `update`
   the survivor (read the full `memory://<id>` first — previews truncate) and
   `invalidate` the losers.
5. `forget` only pure noise with zero historical value; otherwise `invalidate`.
6. Return a ledger: every id, operation, one-line reason.

The first grooming run is propose-only: if no prior report has a "Memory grooming"
section, apply nothing and put the full proposed ledger in the report for review.
Later runs apply directly, except a run proposing more than 20 mutations presents
the list and waits for approval.

Save reports as `YYYY-MM-DD.md` in `~/.dotfiles/.claude/session-analysis/`, with the
grooming ledger under a "Memory grooming" heading. If there are no flags, report that
healthy result; if an agent fails, continue and record it. End by suggesting the user
run `/memory enqueue` to consolidate the groomed state.
