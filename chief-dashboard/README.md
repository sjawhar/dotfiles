# Streams dashboard

Sami's page for the work streams (dispatch://INFRA-202): each stream's main numbers against their targets, whether
each lead's loop is working, what changed since he last looked, a link into each lead's live conversation in Dispatch
with that stream's explainer videos playing beside it, and a panel to talk to the chief of staff and read what it has
ready for him. His decisions stay in his Dispatch Inbox (https://dispatch.tail12ec1c.ts.net/):
the page links there and counts, per stream, the open asks that wait on him. The design, element by element, is in
[DESIGN.md](DESIGN.md).

Live, tailnet only: **https://sami-rocqperf.tail12ec1c.ts.net:8446/**

## For stream leads: your row is yours

Nobody edits the dashboard for you. Your stream appears, and changes, when you change two things in Dispatch:

1. **Label your stream issue `stream`** (any project). Every open issue with that label is a row on the page. A
   labelled issue without a scoreboard shows as "No scoreboard yet".
2. **Upload `scoreboard.json` to that issue** whenever a number, the bottleneck, an experiment or a decision for Sami
   changes, and at least daily:
   `dispatch artifact --issue <YOUR-ISSUE> --name scoreboard.json --path scoreboard.json --summary 'scoreboard'`.
   The format is `~/src/scrum/workstreams/scoreboard-schema.md` (versions 1 and 1.1), plus one field:
   `"headline"`: one sentence, at most 160 characters, overwritten at each update: the one thing Sami should know
   today. The page shows it on your row.
3. **Plans name Dispatch issues.** Each item of `today.items` and of the new `week` list (`{"start": "<Monday,
   Pacific>", "items": [...]}`) names the issue it delivers (`"issue": "OSSV-150"`). The page shows each item's issue,
   priority, status and owner (claim, else route holder, else assignee), linked to the owner's live conversation in
   Dispatch. The full contract: `/mnt/nvme/sami/chief/design/scoreboard-week-contract.md`.

Your row shows, from that file: your name, lead and issue; the headline; **your first main number** (order `numbers`
by importance) with its value, the change since its previous reading and a bar from its first value to its target; a
status chip for every main number; the bottleneck; and the loop's flags. The page re-reads every 2 minutes and shows
a change on Sami's open page within seconds of that read.

**Check the file before you upload it.** This prints the file's format errors and exactly the row and flags the page
will show for it (the same code the page runs):

```sh
uv run --project ~/.dotfiles/chief-dashboard chief-dashboard check scoreboard.json
# without uv, standard library only:
PYTHONPATH=~/.dotfiles/chief-dashboard python3 -m chief_dashboard check scoreboard.json
```

It exits 1 on a format error. Flags (stale scoreboard, loop stalled, fast number never measured or stale or failed
validation, main number without a target or without a guard, experiment without a `before` or a `predicted` value,
no bottleneck or no evidence, no headline) are warnings to act on. A decision for Sami is an ask in Dispatch
(`dispatch ask`), which reaches him in his Inbox; the dashboard shows no queue of decisions and reads no
`needs_sami` field.

The chief of staff's `~/src/scrum/workstreams/streams.json` is optional and overrides only two things,
matched by `issue` or `slug`: `rank` (priority; an unranked stream sorts after the ranked ones) and `attention`
(one of Sami's attention items, starred and sorted first).

## The stream pilot

`chief-dashboard pilot` runs a work stream without a standing lead session (`chief_dashboard/pilot.py`). The stream's
state is the Dispatch document `stream.md` on its stream issue; local state lives under `--state DIR`, one directory
per stream. Nothing here writes to Dispatch or sends an Envoy message: `summary` starts the omp session that does.

- `facts --stream KEY --state DIR [--now ISO] [--pilot-start ISO]` prints one JSON snapshot and writes it to
  `DIR/KEY/facts-<UTC>.json`: the record (saved to `DIR/KEY/stream.md`), the latest scoreboard, each `Order of work`
  row with its owner column's text, its issue's title, status and claim and whether the claim holder is live, each
  open commitment with its line, link, issue title, whether it is overdue and whether its owner session is live
  (`owner_live`), the `LEDGER` comments since the last run, Envoy role holders (`envoy ps --json`) with the record's
  next holder, sessions that claim more than one open issue of the project, and a `findings` list. A row counts as
  started, not ready, while no live session claims it and its latest `started.tsv` line is under 2 hours old or an
  omp process still runs in its tmux pane, read from /proc as `chief-dashboard sessions` reads it (`agent-new` leaves a
  bash in the pane after omp exits, which does not count; tmux names both `bash`, since `omp` is a shim). A row whose
  issue, or an ancestor through `parent`, has the `legion` label is never ready (`legion: true`): the Legion daemon
  routes that tree, and an outside session's claim takes it out of the workflow. Each multi-claim
  entry carries `pilot_session`: whether that session posted a `LEDGER start` on the stream issue since the pilot
  start (2026-11-03T14:30Z unless `--pilot-start`); the set accumulates in `DIR/KEY/pilot-sessions.txt`.
  `pending_offline` lists the lines of `offline-ledger.md` and `pending_offline_decisions` the entries of
  `offline-decisions.md`. An issue with no `stream.md` gives `record: null` and the copy is removed. While Dispatch is
  down the snapshot uses the saved copy and the offline files, reads nothing else from Dispatch, marks no row ready and
  leaves `last-facts` alone; an unreachable Envoy gives role liveness `null`. `sessions` lists the stream's live
  sessions on every machine, each with its Envoy host and title, as `census` counts them. In an agent box, started
  panes are not checked (its /proc shows no host process), so a start counts for its 2 hours only.
- `summary --stream KEY --state DIR --window WINDOW [--dry-run]` runs `facts`, then
  `omp -p --no-title --max-time 30m --max-cost 15` in `DIR/KEY`, logging to `DIR/KEY/summary-<UTC>.log` and exiting
  with omp's code. The prompt tells the session to start sessions only with `agent-new --box --window WINDOW` (which
  makes the window on a machine that has none yet) and to append `ISSUE<TAB>pending<TAB>UTC` to `started.tsv` before
  each `agent-new` and `ISSUE<TAB>PANE<TAB>UTC` after. With no record it starts nothing and logs
  `no stream record, skipped`. `--dry-run` prints the stream's live sessions and the command instead.
- `schedule --jobs FILE --state DIR [--max-jobs N]` runs until stopped. Start it as a named omp background service,
  not a systemd unit. Each job fires at each local `at` time and, with `dues_from`, at each `due` in that stream's
  record. Every cycle re-reads the jobs file and the record from Dispatch (an outage keeps the saved copy; a 404
  removes it) and recomputes the fires from them, so a due the owner removed no longer fires and a moved one fires at
  its new time. Different jobs run at the same time, up to N at once (default 4); a fire that comes due while N jobs
  run starts when one ends. Each job runs one instance at a time: a fire of a job that comes due while that job
  runs, a due included, starts when it ends. Only times already past when the process started are dropped. Fires of
  one job at one time run once, and every due they cover goes into `DIR/<stream>/fired-dues.json`, so a restart does
  not fire it again; only the main thread writes that file, once for all the jobs that ended together, by renaming a
  complete copy over it. It waits at most 10 minutes between cycles, and wakes early for a fire time or a job's end.
  Each job runs in its own process group: at its timeout (`timeout_s`, a positive integer of seconds) the whole group
  is killed and reaped (exit 124) before the job counts as ended, and so is a group whose wait fails. Whatever ends
  the scheduler (an error, SIGTERM, the heartbeat's death) first kills and reaps every running group. A thread writes
  `DIR/heartbeat` (UTC time, pid) every 60 s; the process exits 1 if that thread dies, so a file older than 15
  minutes means the scheduler is down.
- `census --stream KEY --state DIR [--out FILE]` prints the session ids of the stream's live top-level sessions on
  every machine, one per line, to freeze a baseline. A session is the stream's when this machine sees it in one of the
  stream's tmux windows (`sessions.py`), or sees its old pane there, which `agentbox move --to` leaves saying where
  the session went; when an earlier read saw either (`DIR/KEY/members.txt`, which each read extends); or when it is
  in `pilot-sessions.txt`. One in a window here is live by that reading, any other while `envoy ps` lists it. The
  record is what counts the stream on sami-devbox, where a moved session runs in tmux session `moved` in a window
  named for its id, and from an agent box, which sees no host process. What could not be read goes to stderr.
- `measure --stream KEY --start ISO --end ISO [--sessions-file FILE] [--json]` counts, per session and over
  [start, end) in INFRA, BET, LINUX and OSSV: issues claimed and created, sub-issues of its own work, work moved
  (`LEDGER handoff`, a claim released before done), dones with an own child still open, dones without evidence, asks
  opened, and whether it held one issue. Without `--sessions-file` it measures the sessions that posted
  `LEDGER start` on the stream issue in the window. A done is the session's when it made the change or held the claim.
  Every such close is judged, and an issue counts when any of its closes violates: a child counts against a close only
  when the session had created it, it existed with that parent at the close and was not done then. Evidence is a link
  in that close's reason message, or in the session's `LEDGER done` line for the issue when that close is the first at
  or after the line (any https link but a Dispatch issue page, or a `dispatch://` reference to a document, comment or
  message). A link on the issue itself is never evidence. `exit_after_done` is always null: it needs transcripts.

Every time argument is ISO 8601; an offset is converted to UTC, and a time without one is UTC.

The jobs file is JSON with a named time zone (DST-correct through `zoneinfo`):

```json
{"timezone": "America/Los_Angeles", "jobs": [{"name": "summary", "at": ["06:30", "14:30"], "dues_from": "INFRA-167", "command": ["chief-dashboard", "pilot", "summary", "--stream", "INFRA-167", "--state", "/state", "--window", "rocq"], "cwd": "/state/INFRA-167", "timeout_s": 1800}]}
```

## What it reads

All real data, re-read every 2 minutes. The page updates itself live: the server pushes its version over a
WebSocket (`GET /ws`) whenever the read data or a file the page reads changes, and the page fetches its fragment and
changes only what changed, in place. A fragment that is the one already shown changes nothing at all. It keeps the
scroll position, open rows, a playing video and everything a form field holds: what he typed, the caret and the
selection, and the height he dragged a note box to. It never reloads itself: a new build of the page's own code
stops the updates and shows a `New version: reload` link in the pill at the bottom right, and only a click on it
reloads.

- Stream issues: `GET /api/v1/issues?label=stream&open=true`, then each one's `scoreboard.json`, every version.
- Overrides: `streams.json` above (`STREAMS_REGISTRY` sets its path).
- Open asks of every Dispatch project (`GET /api/v1/asks/open?project=`): counted per stream (on the stream issue
  or its `scope_issues`) when the ask waits on a person and its issue is assigned to sami@theorem.dev or has no
  assignee in INFRA, BET, LINUX or OSSV. They are not listed: that is his Dispatch Inbox.
- Live sessions (`GET /api/v1/agents`) for each lead's last Dispatch activity.
- Live agent sessions on this machine (`chief_dashboard/sessions.py`), measured on every read, never declared by a
  lead: every top-level `omp` process (argv[0] `omp` or an oh-my-pi command line, no `omp` ancestor, not an
  `__omp_worker_*` helper), from `/proc`, `tmux list-panes -a` and `docker ps`. A session counts in its pane's
  window of the `agents` tmux session; one inside an agent box has no pane, so it counts in the window of its host
  `docker run --name <box>` (container from its cgroup). `WINDOW_STREAMS` there maps each window to its stream issue
  (dispatch://INFRA-202/spec, "Windows on sami-rocqperf"): a new window is one line. Any other window, a pane in
  another tmux session, or no pane at all is "outside a stream window". `chief-dashboard sessions` prints what the
  page counts; `--record FILE` writes the reading as a test fixture, cut to flags and ids.
- Explainer videos: every `explainer*.mp4` artifact on a stream issue (latest version), titled by the `title:` front
  matter of `<same name>-script.md` when it exists, else by the file name. Dispatch serves files only with the
  machine token, as downloads, without Range support, so the page cannot link there: each version is downloaded once
  to `~/.local/state/chief-dashboard/videos/<sha256>.mp4` (older versions are deleted after a complete read) and
  played from `/video/<sha256>.mp4` with Range support. Each stream's videos play from a dropdown beside its note
  box, newest first; two rows with the same sha256 are one video. There is no separate videos page.

## The front page

The header (its Decisions count links to Sami's Dispatch Inbox, and it links the standup room at `/standup`), the
chief of staff's panel (a note box that sends to it over Envoy, and a link into its live conversation in Dispatch),
every main number that is behind its line across all streams (the one list to read first), the learning loop's
newest round, then the streams in rank order. The header's Agent sessions tile counts every live agent session and
those outside a stream window, and links to the strip under the behind list that counts them by window and names
each one outside, with its window (or tmux session) and its pane title. Each stream's header line carries its own
count ("13 sessions in tasks"), or "no tmux window of its own".

Each stream shows its header line (the decisions waiting on Sami, any links its scoreboard lists, and the plan's
merged marks) and one row per main number. The row's name is a full-width line of ordinary text with the Pace
verdict at the end of it — never a column of a table (Sami, 2026-10-26: "You keep making this mistake where you try
to stick long prose into tables ... That's just never going to work"). Under the name stand the three figures that
are compared, in one right-aligned tabular column with the words that qualify each: the newest reading (large),
where the pace line says the number should be today with the gap to it, and the target with its date, what is
needed per day, the days left and which direction is better. Beside them is the chart, one geometry for every row
on the page, from the first reading to now, with the pace line and the target named at their own right-hand ends
(the target is named at the top right instead when it is out of range) and the gap between the reading and the line
shaded. Colour marks only the verdict: the row's left edge, its pill and its gap words. A guard that got worse
shows in full under its row; the others fold into one line. Under the rows, side by side and always open, a
dropdown of the stream's videos over one player, and beside it a note box to the lead (room for a paragraph, taller
as he types, resizable, up to 20,000 characters) with a link into the lead's live conversation in Dispatch
(`/agents/<session>/live`; on a phone the player comes first). The plan, this week's plan, the bottleneck and the
last 7 days follow in a `<details>` that starts closed.

The learning loop's round (`/mnt/nvme/sami/chief/rounds/<UTC date>T<HH>.md`, its last `# FINAL` section, and
`<same>-adoptions.md`) shows each change sent to other streams with its numbers, each recipient's answer (adopted, not
adopted, no reply), the leads that missed the round (its `Missed:` line), and links to the round's files, served at
`/rounds/<name>`.

The page holds no asks, answers, replies or deliverables: those live in Dispatch (Sami, 2026-10-26 04:53Z: "Please,
let's just use dispatch, which already has all of this. Please do not invent a new one."). It counts Sami's decisions
and links his Dispatch Inbox.

The Dispatch URL and token, and the Envoy hub URL, come from `~/.config/legion/envoy.json`. The page is standard-library
Python; only `chief-dashboard sami-messages` needs PyYAML (in `pyproject.toml`; the system python3 has it).

## Run, restart, remove

The code lives in the dotfiles at `~/.dotfiles/chief-dashboard`, which every machine has a copy of
(dispatch://INFRA-906). It runs as the systemd user service `chief-dashboard` on the unix socket
`/run/user/1000/chief-dashboard.sock`, published to the tailnet by `tailscale serve`. Lingering is on for the user,
so it starts at boot; `tailscale serve --bg` settings persist across reboots.

Install (once):

```sh
cp deploy/chief-dashboard.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now chief-dashboard
sudo tailscale serve --bg --https=8446 unix:/run/user/1000/chief-dashboard.sock
```

Restart after a code change: `systemctl --user restart chief-dashboard`.
Status and logs: `systemctl --user status chief-dashboard`, `journalctl --user -u chief-dashboard -f`.
Is the data fresh: `curl -s --unix-socket /run/user/1000/chief-dashboard.sock http://x/healthz` (prints `read_at`).

Remove:

```sh
sudo tailscale serve --https=8446 off
systemctl --user disable --now chief-dashboard
rm ~/.config/systemd/user/chief-dashboard.service && systemctl --user daemon-reload
rm -rf ~/.local/state/chief-dashboard   # Sami's last-look times and the notes he sent
```

## Who may act as Sami

Anyone on the tailnet can read the page. Two things act as Sami, and are accepted only when the socket's peer is
tailscaled (uid 0, checked with `SO_PEERCRED`; every agent here runs as uid 1000) and the `Tailscale-User-Login` header
is sami@theorem.dev, from any of his devices:

- **A note** (`POST /note`, which also needs the page's own Origin): sent over Envoy as a message from Sami, to the
  chief of staff (`stream=chief`, from the front page's panel) or to a stream's lead (the session its scoreboard names,
  else the session claiming its issue; `/v1` still has these boxes). Every note and its delivery result is kept in
  `~/.local/state/chief-dashboard/notes.jsonl`.
- **"Since your last look"**: page loads are recorded in `~/.local/state/chief-dashboard/looks.json`. Loads less than
  30 minutes apart are one visit; "what changed" starts at the end of the previous visit. Without a recorded visit it
  shows the last 24 hours, and says so.

## Rules the page applies

In `chief_dashboard/model.py`: status of a main number (met, on track or behind by a least-squares line to the end of
the target date, baseline only, not measured), stale scoreboard (24 h), stale fast number (48 h) and stalled loop
(48 h). A stream with no scoreboard shows "No scoreboard yet" and no numbers.

## Development

```sh
uvx --python 3.12 --with pytest pytest -q          # tests, on a synthetic fixture under tests/
uvx ruff check .                                   # lint
python3 -m chief_dashboard snapshot /path/snap.json  # one read of every source, as the page sees it
```

The review screenshots of the build (`shots/`) and the 81 commits this code was written in stayed behind when it
moved here: the old checkout is `~/src/chief-dashboard` on sami-rocqperf, and a bundle of its whole history is at
`/mnt/durable/sami/chief-dashboard-history.bundle`.
