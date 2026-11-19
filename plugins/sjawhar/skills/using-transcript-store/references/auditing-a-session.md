# Audit one session for a person's instructions

Every instruction a person gave one session since a time, newest first, with the `FILE:LINE` of each:

```bash
tq search --human --session <full session id> --from '2026-10-24 06:00' --limit 500
```

Each row is one thing the person did: the time, the session, the `role` (`user` typed, `custom:envoy-message` from Dispatch), the raw line, and their words. Then, for each row, take three or four words of it and read what the session did next:

```bash
tq next '<three or four words of the row>' --human --session <id> -n 8
```

- Count the rows (`| wc -l`) before you report. A session that has worked with a person for a day has tens of them; one or two rows means the window or the session id is wrong, not that the person was quiet.
- `--from` with no `--to` reads to now. Set both when you compare two sessions.
- `tq search --human` does not return a person's words that an agent relayed (`role = 'user:agent'`, or an Envoy message from another session that quotes them): read them with the query below. The last ~10 minutes are not in the store yet: read the session's `.jsonl` from the timestamp of the newest row `tq` returned.

  ```bash
  tq sql "SELECT ts, role, raw_file, raw_line, substring(replaceRegexpAll(text, '\s+', ' '), 1, 300) AS text
          FROM messages
          WHERE session_id = '<id>' AND ts >= '<start>'
            AND (role = 'user:agent' OR role = 'custom:envoy-message')
            AND positionCaseInsensitive(text, '<their name or email, e.g. sami@theorem.dev>') > 0
          ORDER BY ts"
  ```
