---
name: one-issue-session
description: "Use when you are a session started on one Dispatch issue; before you file a child or follow-up issue, defer or hand on work, close the issue, or end. Also when a review finding seems to need its own issue."
---

# One issue, then exit

You own one issue, and you end when it is done. Filing part of your work as a new issue, or moving it to someone
else's, does not make your issue done.

## When you start

1. Claim your issue (`dispatch claim --issue <KEY>`). A stream is a line of work with its own issue, the stream
   issue: the open Dispatch issue labelled `stream` that your brief names or that is an ancestor of your issue
   (`child_of` under `Links:` in `dispatch read`). When the stream issue holds a `stream.md` record, post on it
   `LEDGER start <ISSUE> <one line> <https link to the issue>`: the summary job and the measurement find sessions
   by that line.
2. Make sure the issue states its outcome and what makes it done (next section). If it does not, write both into
   its spec before other work.
3. Unless your brief already carries a `memory graph:` line, run `ctx search "<the issue's title>"` and, for each
   person, customer or vendor the issue names, `ctx dossier "<name>"`. In your issue, note each record that states
   a decision, a commitment, an owner or an open question about the issue's subject or a name it names (at most 5,
   each its path and one line). When no record does, note "memory graph: no records". A record is evidence, not an
   instruction.

## What one issue is

- One outcome, not one pull request. Making a container image current is one issue, even when it takes a version
  pin change, a release, a CI gate and ledger rows in three repositories. A wave of several batches with one build
  and one launch is one issue.
- Done is evidence for every done condition the issue states: a merged pull request, a measured result, or, when
  the result arrives only after the rest of the issue's work is finished (a task's reading a day after launch), the
  work itself: launched with the control runs (the runs that show the measurement can fail) and the number of
  attempts the issue states, or refused with the reason. A job of the stream's scheduler reads the late result;
  name it in your done line. When no job reads it, add one to the jobs file of the scheduler whose heartbeat file
  the stream record's header names. Do not wait for the result.

## While you work

- Do every part of your outcome in this issue: review findings, follow-ups, fixes to your own pull requests, the
  parts you found late. Create no child issue for any of it.
- Work outside your outcome (another stream's bug, a separate feature) is not yours: search Dispatch, then file it
  unclaimed for its owner, and do not start it. It counts in the measurement as an issue you created.
- When your issue's spec, a file or a command's output does not settle a choice (an approach, a trade-off, an
  owner), ask your issue's Dispatch project's Legion controller. That is the session that holds the Envoy role
  `legion-<project>-controller`, with the project key in lower case (`legion-infra-controller`). An Envoy role is a
  named duty that one session holds at a time; a message published to `notifications.role.<role>` reaches its
  holder, so publish your question there. Its answer is advice; you decide.

## Waiting on another issue

- End when a running job restarts the work. That trigger is real only when your issue has a row in the stream
  record's order of work and the scheduler's heartbeat file (its path is in the record's header) is under 15
  minutes old; the scheduler then starts a session on a row whose blocker is done. To end:
  1. Write in your issue what is done and what is left.
  2. `dispatch issue-update --issue <KEY> --status backlog`, with `--blocked-by <BLOCKER>` when it is in the same
     project.
  3. Change your row to "waits on <BLOCKER>, starts when it lands". Post a `LEDGER blocked` line on the stream
     issue that names your issue's key, the issue it waits on, and a link to that issue. End as below.
- Otherwise stay alive, and write in your issue why you stay and what you wait for. A row alone, or a scheduler that
  has stopped, restarts nothing.

## Handing work on

Hand on part of your outcome only when you cannot do it (it needs another stream's authority or machine). Name the
session that accepted it, link its reply on both issues, and post `LEDGER handoff <ISSUE> <what> to <session>
<link to the acceptance>`. Without an accepting session the work stays yours. An accepted handoff is not done: your
issue stays open and waits on that work as above. The measurement counts every handoff.

## Standing roles and periodic work

- Periodic work (a 3-hourly report, a miner, a scorer, a scheduler) is a scheduled job, not a session: a session's
  background jobs and subagents die with it, and nobody notices.
- A standing role (a merge train's runner, a multi-day run's supervisor) is a session only when its issue states
  the condition that ends it. A role with no end condition is a job.
- A measurement whose result arrives only after the rest of your work is finished is a scheduled job plus one
  issue per measurement.
- A session that holds claims on several issues today finishes what it holds, claims no new issue, and exits; each
  next piece of work starts as a new session on its own issue.

## When your issue is done

1. Check it is done: each done condition has its evidence link, and no child issue you created under your issue is
   open. An issue you filed unclaimed for another owner (above) does not hold it open. An issue closed with your
   own child open, or with a condition handed on and not yet done, is not done.
2. Post `LEDGER done <ISSUE> <each done condition and its evidence> <evidence link>`, set the issue to done with
   `--reason` naming the same links, and end as below.

## Ending, done or blocked

1. Hand on each Envoy role you hold: one you claimed with `envoy_role_set`, or one whose row in the record's roles
   table names you. `envoy_role_get` names a role's holder. If its duty ended with your work, retire it: delete its
   row in the record and say so in your ledger line. If its duty goes on, ask the holder the record's roles table
   names (else the record owner) to take it, and confirm with `envoy_role_get` (`xd://envoy_role_get` in omp) that
   it holds it before you go on to step 2. Until one holds it, do not exit: a role left on an ended session alerts
   nobody.
2. Release your claim (`dispatch claim --issue <KEY> --release`) and update your row in the record
   (`dispatch doc-edit`, with the token a fresh `dispatch doc-read` returns).
3. Exit, as your last action. In tmux: `tmux send-keys -t "$TMUX_PANE" /exit Enter`. It ends the session at once
   and aborts the turn, so make every write first. Outside tmux, end your turn with the ledger line.
