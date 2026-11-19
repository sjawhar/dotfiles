---
name: using-transcript-store
description: "Use when you are about to read, grep or script over agent session transcripts (`~/.omp/agent/sessions/**/*.jsonl`, subagent transcripts, session artifacts): where agents spent time, what a person or an agent said and when, what a session did after a message, `tq`."
---

# Using the transcript store

Session transcripts from many hosts load into one ClickHouse store, read with `tq`. It answers in about a second what a script over the raw files takes minutes for, so query `tq` first and open a raw file only at the `FILE:LINE` it gives. `tq --help` and `tq <command> --help` give every option, how search splits text into words, and which rows `--human` keeps: read them before you write a query.

| Question | Command | Default window |
|---|---|---|
| Tool time by command class, per session | `tq time` (`--by host`) | 14 days |
| Sessions with the most tool time | `tq top --limit 20` | 14 days |
| Who said a phrase, and when | `tq search "<phrase>"` (`--human`, `--kind user`) | all time |
| Every message a person sent a session | `tq search --human --session <id> --from <time>` | all time |
| A phrase in commands or tool arguments | `tq search --commands "<phrase>"` | all time |
| What a session did after a message | `tq next "<phrase>" -n 5` | all time |
| Anything else | `tq sql "SELECT ... FROM calls ..."` (read-only) | none |

- Set the same window on every command you compare (`--days N`, or `--from` and `--to`): the defaults differ.
- Without `--host`, `tq time` and `tq top` count every host in the store. List the hosts with `tq sql "SELECT host, count() FROM sessions_latest GROUP BY host"`.
- `tq sql "DESCRIBE TABLE messages"` names the columns.
- `tq` not found: install it as `transcript-store/README.md` in theorem-labs/agent-infra says. That README also has the tables and operations.

## Read the numbers correctly

- Waiting is tool time. `tool:wait`, `tool:task` and async `bash` calls hold the clock while subagents or jobs run, so a session that waited reads as slow. Read the class column, or leave the waits out:

  ```bash
  tq sql "SELECT session_id, round(sum(duration_s)) AS s FROM calls
          WHERE started_at >= now() - INTERVAL 7 DAY AND tool NOT IN ('task', 'wait', 'ask')
            AND NOT ifNull(is_async, false)
          GROUP BY session_id ORDER BY s DESC LIMIT 10"
  ```

- One hung call can run for hours. Read the top rows of `ORDER BY duration_s DESC` on `calls` before you rank sessions.
- Count what a person sent with `--human`, not with `kind = 'user'` rows: briefs and relays from agents are `role = 'user:agent'`, and what a person writes in Dispatch arrives as `custom:envoy-message` rows.
- A `--human` hit matches what the person wrote, not the envelope, so an event with no words (an approval with no reason) matches no phrase. List it with `tq search --human` and no phrase: it shows as its type, such as `[ask.resolved]`.
- `kind = 'file'` rows hold artifacts: full tool outputs, `*.shake.log` originals of pruned results, `local/` files. Their `role` names the file type.
- A subagent's first `kind = 'user'` row is its brief, and its `role = 'task_context'` row is the shared context of the `task` call that started it. `parent_session` in `sessions_latest` links it to its parent.

## Audit one session for a person's instructions

1. List what the person sent, newest first: `tq search --human --session <full session id> --from '<start>' --limit 500`.
2. Count the rows (`| wc -l`). A session a person worked with for a day has tens of them; one or two means the window or the session id is wrong.
3. For each row, take three or four of its words and read what the session did next: `tq next '<words>' --human --session <id> -n 8`.
4. `--human` leaves out a person's words that an agent relayed. Read those with:

   ```bash
   tq sql "SELECT ts, role, raw_file, raw_line, substring(replaceRegexpAll(text, '\s+', ' '), 1, 300) AS text
           FROM messages
           WHERE session_id = '<id>' AND ts >= '<start>'
             AND role IN ('user:agent', 'custom:envoy-message')
             AND positionCaseInsensitive(text, '<their name or email>') > 0
           ORDER BY ts"
   ```

5. Read the session's `.jsonl` after the time of the newest row `tq` returned, since the store trails the live files.

## When the store does not have it

- The store trails the live files, by about ten minutes when loads run. `tq` warns on stderr when a host's last load is over 30 minutes old; that host's newer lines are only in its raw files.
- Read raw files by hand only for what the store lacks, and never scan the whole sessions tree on sami-rocqperf: other work shares its CPU and disk.
