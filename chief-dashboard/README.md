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
newest round, then the streams in rank order.

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
