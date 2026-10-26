---
name: using-transcript-store
description: "Use before you read, grep, jq or script over agent session transcripts: ~/.omp/agent/sessions/**/*.jsonl, subagent .jsonl files and session artifacts. Triggers: where agents spent time, tool-call time or duration, slowest commands, which sessions used the most time, what an agent or a user said and when, find a phrase in past sessions, what an agent did after a message, speed coach, weekly review, librarian, session forensics, tq."
---

# Using the transcript store

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

Every omp session, subagent transcript and session artifact on sami-rocqperf and sami-leanperf is in one ClickHouse store, about 10 minutes behind the live files. `tq` answers in under a second what a hand-written script answers in minutes over 25 GB, so query `tq` first. Open a raw file only at the `FILE:LINE` that `tq` gives you.

| Question | Command | Default window |
|---|---|---|
| Tool time by command class, per session | `tq time` (`--by host`, `--from 2026-10-14 --to 2026-10-21`) | 14 days |
| Sessions with the most tool time | `tq top --limit 20` | 14 days |
| Who said a phrase, and when | `tq search "can't reach 10x"` (`--human`, `--kind user`) | all time |
| Every message a person sent a session | `tq search --human --session <id> --from <time>` | all time |
| A phrase in commands or tool arguments | `tq search --commands "lake build"` | all time |
| What the agent did after a message | `tq next rewrite -n 5` (`--human`) | all time |
| Anything else | `tq sql "SELECT ... FROM calls ..."` (read-only) | — |

Set the same window on every command you compare (`--days N` or `--from`/`--to`): the defaults differ, as the last column shows. `tq <command> --help` gives every option, how search splits text into words, and which rows `--human` keeps; read it before you write a query. `tq sql "DESCRIBE TABLE messages"` names the columns. Output is TSV. `--format JSONEachRow` gives JSON lines.

## Read the numbers correctly

- **Waiting is tool time.** `tool:wait`, `tool:task` and async `bash` calls hold the clock while subagents or jobs run, so a session that waited reads as a slow one. Before you call a session slow, read the class column, or run the time without waiting:

  ```bash
  tq sql "SELECT session_id, round(sum(duration_s)) AS s FROM calls
          WHERE started_at >= now() - INTERVAL 7 DAY AND tool NOT IN ('task', 'wait', 'ask')
            AND NOT ifNull(is_async, false)
          GROUP BY session_id ORDER BY s DESC LIMIT 10"
  ```

- **One call can dominate.** A hung command can run for hours. Run `ORDER BY duration_s DESC` on `calls` and read the top rows before you rank sessions.
- **Parallel calls overlap.** A session's tool time can be more than its wall time, so never divide tool time by wall time for a utilisation figure.
- **Revisions.** In `tq sql`, read `tool_calls` and `sessions` through the views `calls` and `sessions_latest`. The raw tables hold one row for each revision of a call, so a sum over them comes out too high.
- **Most user-role text is from agents.** Parent briefs and IRC relays have `role = 'user:agent'`. `--human` drops them, and keeps both ways a person reaches a session: what they typed (`role = 'user'`), and what they wrote in Dispatch — an ask answer, a comment or reply, a document verdict, an Agents-page message or a broadcast. Dispatch delivers those over Envoy, so they are `role = 'custom:envoy-message'` rows, not `kind = 'user'`: one session had 1 typed row and 50 from Dispatch, so a count of `kind = 'user'` rows can miss nearly everything a person sent. An Envoy delivery whose envelope names an actor that is not a person — another agent, or Dispatch itself — is not human. Use `--human`: a hand-written actor filter in `tq sql` must handle every case above, and `--human` already does.
- **An event with no words of the person matches no phrase.** A `--human` hit matches, and shows, what the person wrote, not the envelope around it. An approval with no reason therefore matches no phrase: list it with `tq search --human` and no phrase, which shows it as its type, `[artifact.approved]`.
- **Words.** Search matches whole words in order. `_ - . ' /` split words, and `’` does not: `can't` matches `can t`, but `can’t` is a different word. Search both forms when the text may hold a curly apostrophe.
- **Files.** `kind = 'file'` rows hold artifacts: full tool outputs, `*.shake.log` originals of pruned results, `local/` files. Their `role` names the file type.
- **A subagent's brief.** Its first `kind = 'user'` row is its brief. The shared context of the `task` call that started it is its `role = 'task_context'` row. `parent_session` links it to the session that started it.

## Audit one session for a person's instructions

To list every instruction a person gave one session since a time, and what the session did next, follow `references/auditing-a-session.md`.

## When the store does not have it

- **Not in the store:** Claude Code logs, other machines, and the last ~10 minutes. sami-leanperf lags more while a timed benchmark runs there. `tq` warns on stderr when a host's last load is over 30 minutes old.
- **`history://<id>` is a condensed view:** `read` and `grep` on it see message text with each tool call and its result collapsed to one line and thinking left out, so a miss there does not mean a tool never ran; for tool output in the last ~10 minutes, grep the agent's `<id>.jsonl` file.
- **`tq` not found:** the host is not a store client. Run the query on sami-rocqperf or sami-leanperf.
- Read raw files by hand only for data the store does not have (the first bullet), and never scan the whole sessions tree on sami-rocqperf: other work shares its CPU and disk.

Setup, tables and operations: `transcript-store/README.md` in theorem-labs/agent-infra.
