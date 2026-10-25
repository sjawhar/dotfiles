---
name: reflect
description: "Use when looking back over a window of agent sessions to find where the agents failed Sami: a retrospective, a weekly or daily review of sessions, recurring corrections, preferences agents keep breaking, automation chances, or a check that last run's fixes held. Also use when a reflection came back with generic lessons, or when deciding which window of sessions to review."
---

# Reflect

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

A run reads every message Sami sent agents in one window, each with the agent work around it, and returns the failures it finds, each with its quotes. Propose a fix only after steps 1 to 5, because a run that starts landing changes reports categories instead of failures.

Scratch goes on the big disk (`/mnt/nvme/<user>/<run>/`), never `/tmp`: records run to tens of MB.

## 1. Enumerate his messages, and pick the window by them

```bash
S=~/.dotfiles/plugins/sjawhar/skills/reflect/scripts
python3 $S/fetch-legion.py --copy-to legion-sessions --from '<start>' --to '<end>' --out legion.jsonl
python3 $S/enumerate-messages.py --from '<start>' --to '<end>' --legion legion.jsonl --out messages.jsonl
```

- Transcripts live in the ClickHouse transcript store, read with `tq` (`skill://using-transcript-store`), which `enumerate-messages.py` queries. Never grep the sessions tree: a grep finds the phrase you guessed, not the message you missed.
- Legion keeps its sessions as files on each issue's PersistentVolumeClaim, not in tq and not in its Postgres, which holds the daemon's issue records only. `fetch-legion.py` copies them out of the running pods, read-only.
- Do not create a pod to read a volume no pod mounts. `fetch-legion.py` prints how many it skipped; put that number in the report, because those sessions are unread.
- The store holds the omp sessions of sami-rocqperf and sami-leanperf only. Claude Code and OpenCode sessions, and other machines, are not in it (`skill://using-transcript-store`): read their files by hand, or name them in the report as unread.
- Pick the window by his messages, not by active time. A window holding none of them returns tool trivia instead of failures.
- Print the enumerator's counts before any reading: messages to read, rows excluded per reason, and whether Legion was read.
- Open five excluded rows per reason and say whether each one should have been excluded; put any real message of his back into `messages.jsonl`. The `agent-sent` and `single-prompt` rules exclude a real message when he writes once to a fresh session.
- Run `scripts/extract-dispatch-human.py --since <start>` too, and add its events: a comment or ask answer of his that no session received is in Dispatch's API and in no transcript.

## 2. Build one record per message

```bash
python3 $S/build-records.py messages.jsonl records
python3 $S/assign-readers.py records --max-records 12
```

- A record is his message in full, with the agent's turns before it and after it, uncut. His words alone cannot show what failed: "A", "Why?" and "False" mean nothing without the turn they answer.
- Never add a cut to these scripts, and never render their records through a summarizer: a 300-character cut per turn loses about half of his words.
- `assign-readers.py` assigns the records, and a reader never picks its own: readers that choose pick the short ones.

## 3. Readers

- Start one fresh subagent per batch. Paste `references/reader.md` whole, then one line naming that batch's record paths in order and its output file.
- Run every reader, the checker, the coder and the synthesis on the session's strongest model, never a cheap tier. A reader that skims returns findings the checker cannot verify, and the coder counts them anyway; fast models do inventory only (counting, globbing, listing).
- A reader may not grep, search `tq` or keyword-match for evidence. The record is its evidence, and a keyword hunt is what makes a run look like an audit without reading one exchange.
- A reader writes its row the moment it finishes a record. Rows written at the end are written from memory and name the wrong causes.
- Read each type's findings back to 2 to 4 of their records before you name a cause, and say what the agent had loaded there.

## 4. Check what was read

- A fresh agent that read no records runs `references/checker.md`. A reader cannot certify its own batch, because a reader that skipped a record also skips reporting it.
- `scripts/check-findings.py` lists records with no row, duplicate rows, quotes that are not in their record and pushbacks that are not in Sami's words, and exits 1 when it finds any.
- Re-run only the batches the checker names, with fresh readers, at most three rounds, and never re-check a record that passed.

## 5. Code the findings into types

```bash
python3 $S/count-types.py export records rows/*.jsonl --out findings.jsonl
python3 $S/count-types.py count records findings.jsonl types.jsonl --prior <last run>/counts.json --out counts.json
```

- Between the two commands, start a fresh agent with `references/coder.md` pasted whole. It writes `types.jsonl`, one type per finding.
- The coder opens the findings with no category list in hand. A fixed codebook returns its own labels and finds no failure they do not already name.
- A type recurs once its findings come from two or more sessions. Only a recurring type may get a fix.
- A pattern of Sami's own (wording agents misread, how he splits work, what he repeats) is reported only when it shows in six or more sessions of the window, because advice mined from one person's few sessions does not hold.

## 6. Fix only what recurs

- Take the first of these homes that would have stopped every finding of the type: delete or rewrite the text that caused it; point to the right text from the skill or tool the agent opens there; add a mechanical gate (a refusal, a lint, a default); add a rule, and `~/.dotfiles/.claude/CLAUDE.md` last of all.
- Write each rule with its reason in one clause, with no incident story, date or quote of his, and no pedantic always or never. Tighten text that already exists before you add a paragraph (`skill://writing-agent-skills`).
- Count last run's types in this run's numbers (`--prior`, rates per 100 records) before you propose anything: held when this run's rate is lower, unchanged within one finding per 100, regressed when it is higher.
- Propose no fix for a type seen in one session. Text costs every session that loads it.
- A fix that changes omp's own behavior ships behind a per-session gate in `~/.dotfiles/omp/extensions/experiments/gates.json`, and `references/experiments.md` measures it.

## 7. Report

- Report, in this order: the window's counts; each type with its count, its sessions, what it cost and two example records, the costliest first; Sami's patterns; the fixes proposed; what the run could not read.
- Give the run's own coverage: messages in the window, records read, findings kept, findings rejected by the checker, records nobody read, and any agent that failed.
- Give each finding its source: the record id and the `FILE:LINE` the record names, so a reader can open the transcript at that line.
- Mark Sami's patterns as observations for him to judge, never as rules.
- Add one line per workstream in the window: what it was trying to do, where its hours went (`tq time --by session`), and the shortest path that would have worked. A failure list with no workstream line reads as generic, whatever the counts say.
- Post the report to Dispatch as an artifact on the issue the run belongs to (`skill://dispatch`), with the evidence files it cites beside it, and keep a copy at `~/.dotfiles/.claude/session-analysis/YYYY-MM-DD.md`. Read `references/presenting.md` before you write the message that carries it.
- Groom memory last (`references/memory-grooming.md`), then `retain` three to five durable facts from the window.
