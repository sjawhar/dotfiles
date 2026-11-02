# The summary session

The scheduler runs `uv run --project ~/.dotfiles/chief-dashboard chief-dashboard pilot summary`, which collects the
stream's facts into a JSON file and starts you with its path, the stream issue and the stream's tmux window. You do a
lead's rounds once, from records, then end. You decide nothing for an owner and approve nothing.

## Steps

1. Read the facts file. Every fact in it was read at its source by the program; read a source yourself only to
   follow a finding.
2. When `record_source` says local copy, Dispatch is down: write the summary to `summary-<UTC stamp>.md` in the
   working directory, send nothing, and end. The next run's summary covers the same ground and replaces it.
3. Post each line of `pending_offline` as its own ledger comment on the stream issue, and add each entry of
   `pending_offline_decisions` to the record's decisions (`dispatch doc-edit`, numbered from a fresh read). Then
   delete from the local files exactly the lines you posted; a line another session added since stays.
4. Write the summary (shape below) and upload it:
   `dispatch artifact --issue <STREAM> --name summary.md --path summary.md --summary 'summary <UTC time>'`.
5. In the record's top, change only the `## Numbers` values (from the scoreboard, each with its date) and the
   `Last summary:` link, with `dispatch doc-edit` against a fresh token. Other lines belong to the sessions that own
   them.
6. For each overdue commitment, send its owner session one Envoy message (`envoy send --message-file - <session>`):
   the commitment line, its link, and "post the done link, or a new due time with its reason, on the issue". When
   the owner session is not live, comment that on the issue instead and list it in the summary.
7. For each issue the facts mark `ready_no_owner`, start one task session, at most two per run. Before each start,
   append `<ISSUE>\tpending\t<UTC ISO>` to `started.tsv` in the working directory; after it, append
   `<ISSUE>\t<pane id agent-new printed>\t<UTC ISO>`. The facts program treats such an issue as started for 2 hours,
   and after that while omp still runs in that pane, so no later run starts it twice:
   `agent-new --window <window> "Read skill://one-issue-session first. Your issue is <https link>. Read it with dispatch read --issue <KEY>, claim it, and take it to done."`
   Before the start, run `ctx search "<the issue's title>"` and `ctx dossier "<name>"` for each person, customer or
   vendor the issue names, and append to that text `memory graph:` and the records that bear on the work (at most 5,
   each its path and one line), or `memory graph: no records`.
   Skip a row whose owner column names a person.
8. For each session in `multi_claim` that posted a `LEDGER start` (a new one-issue session), send it one Envoy
   message: it holds <issues>, and one session holds one issue. For each extra issue that is part of its own outcome,
   copy what only that issue says into its own issue, close the extra one as a duplicate with a link, and release
   its claim; for each that is not, release the claim. Send nothing to a lane that held several issues before the
   pilot: it finishes what it holds.
9. List each role in `roles` with no live holder in the summary, with the holder the record names next.
10. End. Do not wait for replies: the next run reads what changed.

## The summary's shape

At most one screen. Write `https://` links, never `dispatch://`.

```markdown
# <Stream>: summary <YYYY-MM-DD HH:MM> Pacific

**Numbers.** <main: value (date) vs target; guard; fast>. <one line on the bottleneck>.

**Since the last summary.** <one line per ledger event: start, merged, measured, done, blocked, handoff>.

**Needs action.** <overdue commitments, dead claims, ready issues and the session started for each, multi-claim
sessions, roles with no live holder, malformed ledger lines; each with its owner and link. "None" when none.>

**Pilot guard so far.** Sessions <n>; issues created per session <x>; sub-issues of own work <n>; work moved <n>;
done with own child open <n>; done without evidence <n>; held one issue <k/n>.
(`uv run --project ~/.dotfiles/chief-dashboard chief-dashboard pilot measure --stream <STREAM> --start <pilot start>
--end <now>`)

**Standup, at most 200 words.** <yesterday with its number; today with its hour; blockers; what the stream needs
from a named stream.>
```
