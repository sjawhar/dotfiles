# advisor-report

`scripts/advisor-report` measures omp's advisors from the session files on this machine: how often a
watch-mode advisor speaks, whether it speaks inside its charter, how late its held notes arrive, and, for a
pre-send gate, what it decided and how long it took. It computes the same numbers before and after a change
to an advisor, draws labelling samples, and applies the gate's pre-registered go and kill rules. It never
calls a model; labels come from an agent reading a sample packet with the rubric below.

## Running it

```bash
scripts/advisor-report metrics --days 14 --advisor askgate            # text summary
scripts/advisor-report metrics --days 1 --advisor askgate --json      # one JSON document
scripts/advisor-report sample --since 2026-10-01T00:00Z --until 2026-10-08T00:00Z --n 100 --out ~/.omp/advisor-report/sample-w1.jsonl
scripts/advisor-report ingest-labels labels.jsonl
scripts/advisor-report arm gate --launched <ISO of the push that linked the gate> --timeout-ms 90000
scripts/advisor-report readout --check gate --check trial --notify notifications.role.agentc-1305
scripts/advisor-report overlay add advisor.disableRoster askgate      # and `overlay remove …`
```

Inputs: every session file under `~/.omp/agent/sessions` (or `--sessions-root`) touched since the window
opened — root sessions are `sessions/<project>/<stem>.jsonl`, task subagents are the files below
`<stem>/` — each root session's advisor transcript `<stem>/__advisor.<slug>.jsonl` (`--advisor default` is
the unnamed legacy advisor's `__advisor.jsonl`), the `advisor-gate` entries the AskGate extension
(`omp/extensions/askgate.ts`) appends to the root session, and `~/.omp/stats.db` (`--stats-db`, `OMP_STATS_DB`). `metrics` first syncs `stats.db` with the
pinned `omp stats --summary`, as `scripts/omp-billing-watch` does, unless `--no-sync`, and refuses (exit 2)
when the database's newest ingested file predates the window. Unparsable lines are skipped and counted
(`files.bad_lines`). `PI_CODING_AGENT_DIR` moves the agent directory and `DOTFILES_DIR` the dotfiles
checkout, as in `shims/omp`. Windows are `--since`/`--until` (ISO) or `--days N` back from `--until`
(default: the last day).

## Metrics

Shares are fractions from 0 to 1. `-` / `null` means the denominator was empty.

| Metric | Definition |
|---|---|
| `primary_turns` | assistant messages in root sessions inside the window |
| `watch.advisor_turns`, `watch.advise_calls`, `watch.by_ack` | assistant messages and `advise` calls in the advisor transcript; each call is classed by its exact tool-result text: `Delivered.` → `delivered`, `Queued for the end of the turn. Do not re-raise.` → `queued`, `Dropped: empty note.` → `dropped-empty`, `Dropped: nothing actionable.` → `dropped-noise`, `Dropped: already raised.` → `dropped-duplicate`, `Dropped: this update's advice budget is spent.` → `dropped-budget`; anything else is `unknown`, a call with no result `unanswered` |
| `watch.admitted`, `watch.notes_per_100_turns` | delivered + queued notes; admitted / `primary_turns` × 100 |
| `watch.out_of_charter_pct` | admitted notes whose originating update (the advisor-side user message just before the call) matches no `--scope-regex`; the default is `write\(xd://dispatch_(ask\|message\|edit_ask\|comment\|doc_edit\|issue)\)` |
| `watch.held_pct`, `watch.held_latency_ms` | queued share of admitted notes; median of (the first primary advisor card at or after the call whose `details.notes[].note` is the note's exact text) − (the call); `held_unrouted` counts queued notes no card ever carried |
| `watch.moot_pct` | held notes with a card where a primary tool call between the call and the card carries one of the note's ids (a UUID, an 8+-character hex id with a letter and a digit, an `xd://` path) |
| `watch.chains` | per root session, pairs of admitted notes whose word sets have Jaccard ≥ 0.5; sessions with ≥ 2 admitted `blocker`s |
| `watch.cost_usd_per_day` | dollars per day the watch-mode advisor's own turns cost: `stats.db messages.cost_total` for `agent_type = advisor` rows of its transcripts in the window |
| `skips` | primary tool results beginning `Skipped due to pending system advisory` |
| `scoped_calls` | assistant tool calls in any session file that are a `write` whose `path` matches the scope regex (as `write(<path>)`) or a top-level `dispatch_<x>` call that does; `xd://dispatch_issue` counts only when its JSON arguments carry `spec`. Split by where the file sits: `root` (`sessions/<project>/<stem>.jsonl`, the root sessions a gate covers) and `subagent` (any file below `<stem>/`, task subagents, which run no advisor); `per_day` divides by the window length. `stats_db_agent_type` cross-checks that split: for each kind, the calls by `stats.db tool_calls.agent_type` joined on (session file, tool call id), and `unjoined` for calls in no row (stats.db holds no row for some calls of files it has ingested in full). An agent box relaunch moves a session to a new project directory and `stats.db` keeps the rows it ingested earlier under the old path, so the join also tries every ingested path with the same `<stem>.jsonl` or `<stem>/…` tail; `corrections` joins the same way |
| `gate` | over `{type: custom, customType: advisor-gate}` entries whose `advisor` slugifies to `--advisor`: `counts` by `decision/outcome`; `latency_ms_p50`/`p95` (nearest rank) over `outcome: verdict`; `fail_open_rate` = (timeout + error + no-verdict) / (verdict + timeout + error + no-verdict); `ungated_share` = (timeout + error + no-verdict + unavailable + halted) / (matched − rebuttal − breaker − killed), since an override, a breaker pass and the machine kill switch are deliberate passes rather than gate failures; `revise_rate` over verdicts; `rebuttal_rate` over matched; `breaker_trips`; `killed`; `halted_sessions`; `usage` (input, output, cache-read and cache-write tokens, and `cost` in dollars) summed from each entry's `usage`, with `usage_per_day` and `cost_usd_per_day` — the entries are the only record of the gate's spend, which `omp stats` cannot see. Other entry fields (`argsDigest`, `promptBytes`) are not read |
| `corrections` | Σ(negation + blame + anguish + yelling + profanity) over `stats.db user_messages` of the window's root sessions, per 100 primary turns |
| `labelled` | over the fired rows (gate `revise` verdicts and admitted notes) that carry a label: precision = (acted-correct + ignored-advisor-right) / n; harm = (acted-harmful + skips) / n |

## Sample packets and labels

`sample` draws from the window's gate decisions and admitted notes. The priority stratum is every gate
`revise` verdict and every admitted `blocker` note: all of it when it fits in half the packet, otherwise a
uniform half-packet of it; the rest of the packet is drawn uniformly from everything else. The first line of
the packet is a header recording each stratum's population and draw; every other line is one row:

- `id` — `gate:<session id>:<entry id>` or `note:<session id>:<advise call id>`; labels join on it
- `kind`, `stratum`, `session`, `advisor`, `timestamp`
- a gate row: `decision`, `outcome`, `verdictMode`, `reason`, `rebuttal`, `tool`, `path`, and `call` (the
  tool call's arguments, a device `content` parsed as JSON)
- a note row: `severity`, `ack`, `note`, and `update` (the tail of what the advisor was shown)
- `context` — up to five primary messages either side of the decision (the card, for a routed note)

A labeller writes one line per row, `{"id", "label", "labeler", "at"}`, and `ingest-labels` appends the file
to `~/.omp/advisor-report/labels.jsonl` only when every row is well-formed (an unknown label rejects the
whole file, exit 2). A re-labelled id takes its newest label.

### Rubric

One label per row:

- `acted-correct` — the agent changed the call or retracted it, and the change was right.
- `acted-harmful` — the agent followed a wrong revise: it weakened a legitimate ask, stalled, or skipped
  needed work.
- `ignored-agent-right` — the agent resent or ignored the note, and the original was fine.
- `ignored-advisor-right` — the agent resent or ignored the note, and the advisor was right.
- `moot` — the issue was already resolved when the note arrived.
- `noise` — there was no finding at all.

Rejection share, the secondary outcome: for a uniform sample of 30 asks per window (ask ids come from the
`Asked <uuid> on <KEY>` tool results in the transcripts), read each ask in Dispatch; a rejection is a first
human reply that retracts it, refuses its framing, or asks why he is being asked.

## Readout rules

`arm gate` records the gate's launch time, its timeout and the pinned omp version in
`~/.omp/advisor-report/launch.json`. `readout --check gate` (both checks when none is named) then applies
these rules over the window from launch to now; a gate that was never armed is no finding.

| When | Rule | Result |
|---|---|---|
| any day, before every rule below | `advisor.disableRoster` in the overlay holds `askgate`, or any call since launch recorded `outcome: killed` | killed: no GO or EXTEND is computed and nothing is written (exit 1) |
| day 3 onward | `ungated_share` > 0.20 over ≥ 20 matched calls in the last 24 h, or verdict p95 above 0.9 × the timeout in the last 24 h | KILL |
| day 14 | `ungated_share` ≥ 0.10 over the window | KILL |
| day 14, ≥ 30 labelled revises | precision < 0.3 | KILL |
| day 14, any number of labelled revises | harm > 0.10 | KILL |
| day 14, ≥ 30 labelled revises | precision ≥ 0.5, harm ≤ 0.05, `ungated_share` < 0.10, p95 ≤ 0.9 × timeout, skips = 0 | GO: make `OMP_ASKGATE=block` the shim default |
| day 14–20 | neither | EXTEND one week |
| day 21 onward | not GO | KILL |
| any day | fewer than 30 labelled revises | incomplete; each completed week with no labels is named |
| any day | `~/.omp/agent/extensions/askgate.ts` does not resolve to a file under `$DOTFILES_DIR`, or `$DOTFILES_DIR/omp/WATCHDOG.yml` has no `advisors:` entries | incomplete; nothing is written |

The killed rule comes first because a killed gate records every later call as `killed`, which leaves every
denominator: the rules would judge only the calls before the kill, and a 24-hour burst that tripped the day-3
rule is small against the whole window, so the day-14 rules could pass and read GO. The `killed` entries keep
saying so after the owner's cleanup removes `askgate` from the overlay. The day-3 rule catches a gate that
fails open or stalls the agent. Its latency bound is 0.9 × the timeout, not the timeout itself, because a
verdict slower than the timeout is recorded as a `timeout`, so verdict p95 can never pass it: a gate
answering at 85 s against a 90 s deadline stalls every scoped write and must still trip the rule. The rest is
the go decision. Precision is
measured against a baseline of 4 in 26 notes (0.15) from the watch-mode AskGate. The link rule catches a
gate that is no longer loaded. The roster rule writes nothing because no setting reaches an older omp: a
roster without `advisors:` entries makes an omp that falls back to its every-turn default watcher when the
list is empty run that watcher, and pausing the gate would change nothing there. The readout also reports,
with no rule attached, the gate's spend per day since launch beside what the watch-mode AskGate cost per day
over the 14 days before launch (from `stats.db`). The trial check reports `trial: not armed` until a trial is armed
(`arm trial` refuses without `--approved-by <ask id>`, the recorded approval); an armed trial is incomplete
until its own rules are added here.

The readout applies every KILL itself, by `overlay add advisor.disableRoster askgate`. The exit code is 1
when any check applied a KILL, else 3 when any check is incomplete, else 0. With `--notify TOPIC` it sends
one Envoy message covering every check, beginning `advisor-report (AGENTC-1323)` so the role holder can tell
it apart from other watchers on the same role, through `scripts/envoy send --source envoy`, so the message
comes from envoy rather than from a person or whichever session ran it. It first runs `scripts/envoy send
--source` alone, which an envoy with the flag refuses with `--source accepts only envoy`; any other answer
means the flag is missing, and an envoy without it would publish to a topic named `--source`, so nothing is
sent. A missing flag and a failed send each raise the exit code to at least 3, after any KILL is written. It
is meant to run daily from a oneshot user timer, where a non-zero exit leaves the unit failed.

## The overlay

`~/.omp/agent/local-overrides.yml` is a machine-local settings overlay; `overlay add <key.path> <member>` and
`overlay remove <key.path> <member>` are its only writers. Each holds `flock(LOCK_EX)` on
`local-overrides.yml.lock` for the whole read-modify-write, keeps every other key and list member, writes a
temp file in the same directory, fsyncs it, renames it over the file and fsyncs the directory, so a crash or
a concurrent writer never leaves a torn or stale file. Adding a present member or removing an absent one
changes nothing. Owners remove only their own members: the gate's kill is `askgate`; turning every advisor
off on this machine is `askgate`, `memory` and `drift`.
