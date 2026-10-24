---
name: using-transcript-store
description: "Use before you read, grep, jq or script over agent session transcripts: ~/.omp/agent/sessions/**/*.jsonl, subagent .jsonl files and session artifacts. Triggers: where agents spent time, tool-call time or duration, slowest commands, which sessions used the most time, what an agent or a user said and when, find a phrase in past sessions, what an agent did after a message, speed coach, weekly review, librarian, session forensics, tq."
---

# Using the transcript store

Every omp session, subagent transcript and session artifact on sami-rocqperf and sami-leanperf is in one ClickHouse store, about 10 minutes behind the live files. `tq` answers in under a second what a hand-written script answers in minutes over 25 GB. Query `tq` first. Open a raw file only at the `FILE:LINE` that `tq` gives you.

## Quick reference

| Question | Command | Default window |
|---|---|---|
| Tool time by command class, per session | `tq time` (`--by host`, `--from 2026-10-14 --to 2026-10-21`) | 14 days |
| Sessions with the most tool time | `tq top --limit 20` | 14 days |
| Who said a phrase, and when | `tq search "can't reach 10x"` (`--human`, `--kind user`) | all time |
| A phrase in commands or tool arguments | `tq search --commands "lake build"` | all time |
| What the agent did after a message | `tq next rewrite -n 5` (`--human`) | all time |
| Anything else | `tq sql "SELECT ... FROM calls ..."` (read-only) | — |

Set the same window on every command you compare (`--days N` or `--from`/`--to`). `tq <command> --help` gives every option and the word rule. Output is TSV. `--format JSONEachRow` gives JSON lines.

## Read the numbers correctly

- **Waiting is tool time.** `tool:wait`, `tool:task` and async `bash` calls hold the clock while subagents or jobs run. Read the class column before you call a session slow. Time without waiting:

  ```bash
  tq sql "SELECT session_id, round(sum(duration_s)) AS s FROM calls
          WHERE started_at >= now() - INTERVAL 7 DAY AND tool NOT IN ('task', 'wait', 'ask')
            AND NOT ifNull(is_async, false)
          GROUP BY session_id ORDER BY s DESC LIMIT 10"
  ```

- **One call can dominate.** A hung command can run for hours. Look at the longest calls (`ORDER BY duration_s DESC` on `calls`) before you rank sessions.
- **Parallel calls overlap.** A session's tool time can be more than its wall time.
- **Most user-role text is from agents.** Parent briefs and IRC relays have `role = 'user:agent'`. `--human` keeps only messages a person typed.
- **Words.** Search matches whole words in order. `_ - . ' /` split words, and `’` does not: `can't` matches `can t`, but `can’t` is a different word. Search both forms when the text may hold a curly apostrophe.
- **Files.** `kind = 'file'` rows hold artifacts: full tool outputs, `*.shake.log` originals of pruned results, `local/` files. Their `role` names the file type.
- **Revisions.** In `tq sql`, read `tool_calls` and `sessions` through the views `calls` and `sessions_latest`. The raw tables count one call once per revision.

## When the store does not have it

- **Not in the store:** Claude Code logs, other machines, and the last ~10 minutes. sami-leanperf lags more while a timed benchmark runs there. `tq` warns on stderr when a host's last load is over 30 minutes old.
- **`tq` not found:** the host is not a store client. Run the query on sami-rocqperf or sami-leanperf.
- Only then read files by hand, and never scan the whole sessions tree on sami-rocqperf: other work shares its CPU and disk.

Setup, tables and operations: `transcript-store/README.md` in theorem-labs/agent-infra.

## Common mistakes

| Mistake | Instead |
|---|---|
| A Python script over every `.jsonl` to sum tool time | `tq time` or `tq top` |
| `grep -r` over the sessions tree for a phrase | `tq search`, then read the named line |
| Ranking sessions by raw tool time | Read the `tool:wait` and `tool:task` rows, and the longest calls, first |
| Quoting a `kind = 'user'` hit as the user's words | `--human`, or read `role` |
| Summing `tool_calls` in `tq sql` | Sum `calls`: one row per call |
