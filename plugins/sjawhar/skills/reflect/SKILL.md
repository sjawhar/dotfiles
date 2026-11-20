---
name: reflect
description: "Use when reviewing a window of agent sessions for where agents failed Sami: a retrospective, a daily or weekly review, recurring corrections, broken preferences, automation chances, or whether last run's fixes held. Also when a reflection came back generic, or to choose the window."
---

# Reflect

A run reads every message Sami sent agents in one window, each with the agent work around it, and returns the failures it finds, each with its quotes. Propose a fix only after step 5: a run that starts landing changes reports categories instead of failures.

- Keep scratch on a large disk (`/mnt/nvme/<user>/<run>/`), not `/tmp`: records run to tens of MB.
- Run every reader, checker and coder, and the synthesis, on the session's strongest model. Fast models do inventory only (counting, globbing, listing): a reader that skims returns findings the checker cannot verify.

## 1. Enumerate his messages, and pick the window by them

```bash
S=~/.dotfiles/plugins/sjawhar/skills/reflect/scripts
python3 $S/fetch-legion.py --copy-to legion-sessions --from '<start>' --to '<end>' --out legion.jsonl
python3 $S/enumerate-messages.py --from '<start>' --to '<end>' --legion legion.jsonl --out messages.jsonl
python3 $S/extract-dispatch-human.py --since '<start>' --out dispatch-events.jsonl --text dispatch-events.md
```

- Pick the window by his messages, not by active time: a window holding none returns tool trivia.
- `enumerate-messages.py` reads the transcript store (`using-transcript-store`). Do not grep the sessions tree: a grep finds the phrase you guessed, not the message you missed. The store holds the hosts `tq sql "SELECT host, count() FROM sessions GROUP BY host"` lists, with Claude Code and Codex logs from some of them; read by hand the sessions of any other host, or name them as unread.
- `fetch-legion.py` prints how many Legion volumes no pod mounts. Put that count in the report as unread; do not create a pod to read them.
- Print the enumerator's counts before any reading: messages to read, rows excluded per reason, and whether Legion was read.
- Open five excluded rows per reason and judge each; put any real message of his back into `messages.jsonl`. `agent-sent` and `single-prompt` exclude a real message he writes once to a fresh session.

## 2. Build one record per message

```bash
python3 $S/build-records.py messages.jsonl records
python3 $S/assign-readers.py records --max-records 12
```

- A record is his message in full with the agent's turns before and after it, uncut: "A", "Why?" or "False" mean nothing without the turn they answer. Do not add a cut to these scripts or pass records through a summarizer.
- Code assigns the records; a reader never picks its own, because readers that choose pick the short ones.
- `dispatch-events.md` holds his comments and ask answers, some of which reached no session. Add each of those as a Dispatch-only record (an `M9xxx` id, a `## His message (verbatim)` section, the thread it answers), indexed in `records/records-dispatch.jsonl`, and give those records to a reader too.

## 3. Read

- Start one fresh subagent per batch. Paste `references/reader.md` whole, then one line naming that batch's record paths in order and its output file.
- A reader does not grep, search `tq` or keyword-match: the record is its evidence.

## 4. Check what was read

- A fresh agent that read no records runs `references/checker.md`, pasted whole: a reader that skipped a record also skips reporting it.
- When `check-findings.py` flags more than about 20 items, split them across fresh checkers, each returning a fix or a rejection per item, and merge their answers into one `verdict.json`.
- Re-run only the batches the checker names, with fresh readers, at most three rounds. Do not re-check a record that passed.

## 5. Code the findings into types

```bash
python3 $S/count-types.py export records rows/*.jsonl --out findings.jsonl
python3 $S/count-types.py count records findings.jsonl types.jsonl --prior <last run>/counts.json --out counts.json
```

- Between the two commands, a fresh agent with `references/coder.md` pasted whole writes `types.jsonl`. It starts with no category list: a fixed codebook finds only the failures it already names.
- `count` marks each failure type `recurs` or `once` and each of Sami's pattern types `enough` or `few` (`count-types.py --help`). Report a pattern type only when it is `enough`.
- Before you name a type's cause, read its findings back in 2 to 4 of their records and say what the agent had loaded there.

## 6. Fix only what recurs

1. Compare with last run first (`--prior`, rates per 100 records): held when this run's rate is lower, unchanged within one finding per 100, regressed when higher.
2. Propose a fix only for a type `count` marks `recurs`: text costs every session that loads it.
3. Take the first home that would have stopped every finding of the type: delete or rewrite the text that caused it; point to the right text from the skill or tool the agent opens there; a mechanical gate (a refusal, a lint, a default); a rule, with `~/.dotfiles/.claude/CLAUDE.md` last.
4. Write a rule with its reason in one clause, and no incident, date or quote. Tighten existing text before you add a paragraph (`writing-agent-skills`).
5. A fix that changes omp's own behavior ships behind a gate in `~/.dotfiles/omp/extensions/experiments/gates.json`; measure it as `skill://writing-agent-skills/references/testing.md`, "Measuring a harness change", says.

## 7. Report

1. Order: the window's counts; each type with its count, its sessions, what it cost and two example records, costliest first; Sami's patterns, as observations for him to judge, not rules; the fixes proposed; what the run could not read.
2. Coverage: messages in the window, records read, findings kept, findings the checker rejected, records nobody read, and any agent that failed.
3. Each finding's source: its record id and the `FILE:LINE` the record names.
4. One line per workstream: what it tried to do, where its hours went (`tq time --by session`), and the shortest path that would have worked. Without these lines a failure list reads as generic.
5. Read `references/experiments.md` when a gate in `gates.json` was `random` during the window (`git log -p` over it).
6. Post the report as a Dispatch artifact on the run's issue, with the evidence files it cites, and keep a copy at `~/.dotfiles/.claude/session-analysis/YYYY-MM-DD.md`.
7. In the message that carries it:
   - Lead with the counts and the failure types: he reads the first screen.
   - Bring at most 3 decisions, each as current state, evidence, options with trade-offs, and your recommendation, explained in the message, not by a section reference. Name what is queued behind them. No numbered menu of every open decision.
   - Propose rules one at a time, each with why a cheaper home (a tool, a default, the skill loaded at that step) does not fit.
   - Define every analysis term where you use it (reader names, type names, record ids), and give each count one concrete example.
   - Short, plain sentences. Quote him only as evidence of a finding.
8. Groom memory last (read `references/memory-grooming.md` then), and `retain` three to five durable facts from the window.
