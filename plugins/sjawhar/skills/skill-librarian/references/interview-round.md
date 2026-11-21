# Interview round

Step 4 of every wake in `SKILL.md`. Do these steps in order.

1. Candidates: `~/.omp/agent/sessions/-*/*.jsonl`, except your own (`_<$OMP_SESSION_ID>.jsonl`).
2. Skip a file whose mtime is before its `last interview (UTC)`: it did nothing since.
3. Run `omp-session-active --since-line <last line> <file>`, without the flag for a session with no row (its stretch starts at 1). It is due when `active_seconds_since >= 10800`, or, with no row, `active_seconds >= 10800`. N is `lines`.
4. For each due session, from its last line (or 1) to N:
   - HDO screen first: `omp-session-render grep <file> '\b[A-Za-z0-9][A-Za-z0-9._-]*-hdo\b'`. A hit in the stretch: no consult, no reader, `notes` = `skipped: HDO material`, `last line` = N. Keep this pattern: it needs a character before `-hdo`, so it does not match the bare `-hdo` in the skill's rules, and a session that only quotes those rules still gets its interview.
   - Model: the last `"type":"model_change"` entry. Not `anthropic/`: no consult, reader only, `notes` = `consult skipped: non-Claude session`.
   - Consult: one `task` call with `agent: "seance"`, `sourceSession` = `<file>` (its path, or the session id), and the full text of `references/interview-question.md` as the task text, naming the stretch it covers: lines `<last line>` to N and the UTC timestamp of line N. One task per session; several sessions may go in one `tasks[]` batch. `last line` = N. Discard the answer unless the consult's served model starts with `anthropic/`.
   - Reader: the `task` tool takes no model argument, so first check `~/.dotfiles/omp/config.yml`: `modelRoles.task`, `task.agentModelOverrides.task`, and the `retry.fallbackChains` entry whose key is that model, or the `default` entry if none matches. Any not `anthropic/`: no reader this round, `notes` = `reader skipped: task role not Claude-only`. Else dispatch one `task` agent with the brief in `references/reader-brief.md`: START = last line (or 1), END = N, SCRATCH = a fresh `mktemp -d` for it alone. Afterwards, read its session's last `model_change`, and discard the report unless it is `anthropic/`.
5. Insert or update the Sessions row (session, harness, machine, title, `last interview (UTC)` = now, `last line` = N). Read it back before any later ledger update or routing.
