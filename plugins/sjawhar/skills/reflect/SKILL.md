---
name: reflect
description: "Use when reviewing a window of agent sessions for where agents failed Sami: a retrospective, a daily or weekly review, recurring corrections, broken preferences, automation chances, or whether last run's fixes held. Also when a reflection came back generic, or to choose the window."
---

# Reflect

A run reads every message Sami sent agents in one window, each with the agent work around it, and returns the failures it finds, each with its quotes.

- Propose a fix only after step 5: a run that starts landing changes reports categories instead of failures.
- Keep scratch on a large disk (`/mnt/nvme/<user>/<run>/`), not `/tmp`: records run to tens of MB.
- Run every reader, checker and coder, and the synthesis, on the session's strongest model.
- Use fast models for inventory only (counting, globbing, listing): a reader that skims returns findings the checker cannot verify.

## 1. Enumerate his messages, and pick the window by them

```bash
S=~/.dotfiles/plugins/sjawhar/skills/reflect/scripts
python3 $S/fetch-legion.py --copy-to legion-sessions --from '<start>' --to '<end>' --out legion.jsonl
python3 $S/enumerate-messages.py --from '<start>' --to '<end>' --legion legion.jsonl --out messages.jsonl
python3 $S/extract-dispatch-human.py --since '<start>' --out dispatch-events.jsonl --text dispatch-events.md
```

- Pick the window by his messages, not by active time: set `--from` and `--to` around the day or week of his messages you mean to review.
- Keep the window only when the enumerator counts at least one message to read. A window of agent activity with none of his messages returns tool trivia.
- Do not grep the sessions tree: a grep finds the phrase you guessed, not the message you missed. `enumerate-messages.py` reads the transcript store (`using-transcript-store`).
- Read by hand, or name in the report as unread, each session the transcript store does not hold: one on another host, an OpenCode session, or a Claude Code log it did not copy. The store holds the omp sessions of the hosts `tq sql "SELECT host, count() FROM sessions GROUP BY host"` lists, and Claude Code and Codex logs from some of them.
- Put the count of Legion volumes no pod mounts, which `fetch-legion.py` prints, in the report as unread.
- Do not create a pod to read those volumes.
- Print the enumerator's counts before any reading: messages to read, rows excluded per reason, and whether Legion was read.
- Open five excluded rows per reason and judge each.
- Put any real message of his among those rows back into `messages.jsonl`. `agent-sent` and `single-prompt` exclude a real message he writes once to a fresh session.

## 2. Build one record per message

```bash
python3 $S/build-records.py messages.jsonl records
python3 $S/assign-readers.py records --max-records 12
```

- Do not add a cut to these scripts: a record is his message in full with the agent's turns before and after it, uncut. "A", "Why?" or "False" mean nothing without the turn they answer.
- Do not pass records through a summarizer, for the same reason.
- Code assigns the records; a reader never picks its own, because readers that choose pick the short ones.
- Add each of his comments and ask answers in `dispatch-events.md` that reached no session as a Dispatch-only record (an `M9xxx` id, a `## His message (verbatim)` section, the thread it answers), indexed in `records/records-dispatch.jsonl`.
- Give those Dispatch-only records to a reader too.

## 3. Read

- Start one fresh subagent per batch.
- Paste into each batch's subagent `references/reader.md` whole, then one line naming that batch's record paths in order and its output file.
- A reader does not grep, search `tq` or keyword-match: the record is its evidence.

## 4. Check what was read

- A fresh agent that read no records runs `references/checker.md`, pasted whole: a reader that skipped a record also skips reporting it.
- When `check-findings.py` flags more than 20 items, split them across fresh checkers, each returning a fix or a rejection per item.
- Merge those checkers' answers into one `verdict.json`.
- Re-run only the batches the checker names, with fresh readers, at most three rounds.
- Do not re-check a record that passed.

## 5. Code the findings into types

```bash
python3 $S/count-types.py export records rows/*.jsonl --out findings.jsonl
python3 $S/count-types.py count records findings.jsonl types.jsonl --prior <last run>/counts.json --out counts.json
```

- Between the two commands, a fresh agent with `references/coder.md` pasted whole writes `types.jsonl`.
- That agent starts with no category list: a fixed codebook finds only the failures it already names.
- Report a pattern type only when it is `enough`. `count` marks each failure type `recurs` or `once` and each of Sami's pattern types `enough` or `few` (`count-types.py --help`).
- Before you name a type's cause:
  - Read its findings back in 2 to 4 of their records.
  - Say what the agent had loaded there.

## 6. Fix only what recurs

1. Compare with last run first (`--prior`). For each type in `counts.json`, take its `per_100_records` less its `prior_per_100_records`:

   | When | The type |
   | --- | --- |
   | That difference is below -1 | has held |
   | That difference is from -1 to 1 | is unchanged |
   | That difference is above 1 | has regressed |
   | Its `prior_per_100_records` is null | is new |

   Classify the same way a type that the prior `counts.json` has and this run's lacks: it has a rate of 0 now.
2. Propose a fix only for a type `count` marks `recurs`: text costs every session that loads it.
3. Take the first home that would have stopped every finding of the type: delete or rewrite the text that caused it; point to the right text from the skill or tool the agent opens there; a mechanical gate (a refusal, a lint, a default); a rule, with `~/.dotfiles/.claude/CLAUDE.md` last.
4. Write a rule with no incident, date or quote.
   - Give a reason only where the rule would look arbitrary, in one clause.
   - Write "always" or "never" only for a rule with no exception.
   - Tighten existing text before you add a paragraph (`writing-agent-skills`).
5. Ship a fix that changes omp's own behavior behind a per-session gate in `~/.dotfiles/omp/extensions/experiments/gates.json`.
   - Measure that fix as `skill://writing-agent-skills/references/testing.md`, "Measuring a harness change", says.

## 7. Report

1. Order:
   1. The window's counts.
   2. Each type with its count, its sessions, what it cost him and two example records, costliest first. Its cost is the findings he pushed back on (`pushed_back` in `counts.json`).
   3. Sami's patterns, as observations for him to judge, not rules.
   4. The fixes proposed.
   5. What the run could not read.
2. Coverage: messages in the window, records read, findings kept, findings the checker rejected, records nobody read, and any agent that failed.
3. Each finding's source: its record id and the `FILE:LINE` the record names.
4. One line per workstream (the records that share an `issue` in `records/records.jsonl`, or a `repo` when they have none): what it tried to do, where its hours went (`tq time --by session`), and the shortest path that would have worked. Without these lines a failure list reads as generic.
5. Read `references/experiments.md` when a gate in `gates.json` was `random` during the window (`git log -p` over it).
6. Post the report with `dispatch artifact` (`skill://dispatch`) on the run's issue: the Dispatch issue that asked for this run, or one you file for it when none did.
   - Attach the evidence files the report cites.
   - Keep a copy of the report at `~/.dotfiles/.claude/session-analysis/YYYY-MM-DD.md`.
7. Read `references/presenting.md` before you write the message that carries the report, and before any later message that asks him to decide.
8. Groom memory last (read `references/memory-grooming.md` then).
   - `retain` three to five durable facts from the window.
