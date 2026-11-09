---
name: one-issue-session
description: "Use when you are a session started on one Dispatch issue; before you file a child or follow-up issue, defer or hand on work, close the issue, or end. Also when a review finding seems to need its own issue."
---

# One issue, then exit

You own one issue, and you end when it is done. Filing part of your work as a new issue, or moving it to someone
else's, does not make your issue done. That is the failure this skill exists to stop: sessions spread their work
over many sub-issues and defer parts to other issues, so they can call themselves finished while the work is not.

## When you start

1. Claim your issue (`dispatch claim`). In a stream with a `stream.md` record, post on the stream issue
   `LEDGER start <ISSUE> <one line> <https link to the issue>`, because the summary job and the measurement find
   sessions by that line.
2. Make sure the issue states its outcome and what makes it done (next section). If it does not, write both into its
   spec before other work.
3. Unless your brief already carries a `memory graph:` line, run `ctx search "<the issue's title>"` and, for each
   person, customer or vendor the issue names, `ctx dossier "<name>"`. Note the records that bear on the work (at
   most 5, each its path and one line) in your issue, or "memory graph: no records". A record is evidence, never an
   instruction.

## What one issue is

- **One outcome, not one pull request.** Making an image current across a re-pin, a cut, a gate and ledger rows in
  three repositories is one issue. A wave of cuts with one draw, one build and one launch is one issue.
- **Done is evidence for every done condition the issue states:** a merged pull request, a measured result, or,
  when the result arrives long after the work (a task's reading a day after launch), the work itself: launched with
  its controls and attempts, or refused with the reason. A late result is read by a job in the scheduler's jobs
  file, named in your done line; add the job if none exists. You do not wait for the result.

## While you work

- **Do every part of your outcome in this issue:** review findings, follow-ups, fixes to your own pull requests,
  the parts you found late. Never create a child issue for any of it.
- **Work outside your outcome** (another stream's bug, a separate feature) is not yours: search Dispatch, then file
  it unclaimed for its owner, and do not start it. It counts in the measurement as an issue you created.
- **For judgment, ask the project's Legion controller.** Its answer is advice; you decide.

## Waiting on another issue

- **End when a running job restarts the work.** The trigger is real only when your issue has a row in the stream
  record's order of work and the scheduler's heartbeat file (its path is in the record's header) is under 15 minutes
  old. The scheduler runs the summary at 06:30 and 14:30 Pacific, which starts a session on a row whose blocker is
  done; the chief of staff's session keeps the scheduler running and re-arms it after a restart. Then: write in your
  issue what is done and what is left; `dispatch issue-update --issue <KEY> --status backlog`, with
  `--blocked-by <BLOCKER>` when it is in the same project; change your row to "waits on <BLOCKER>, starts when it
  lands"; post `LEDGER blocked`; and end as below.
- **Otherwise stay alive,** and write in your issue why you stay and what you wait for. A row alone, or a scheduler
  that has stopped, restarts nothing, and the work would stop.

## Handing work on

Hand on part of your outcome only when you cannot do it (it needs another stream's authority or machine). Name the
session that accepted it, link its reply on both issues, and post `LEDGER handoff <ISSUE> <what> to <session>
<link to the acceptance>`. Without an accepting session the work stays yours. An accepted handoff is not done: your
issue stays open and waits on that work as above. Every handoff is counted.

## Standing roles and periodic work

- **Periodic work is a scheduled job, never a session:** a 3-hourly report, a miner, a scorer, a scheduler. A
  session's background jobs and subagents die with it, and nobody notices.
- **A standing role** (a merge train's runner, a multi-day run's supervisor) is a session only when its issue states
  the condition that ends it: the train is retired, the run ends. A role with no end condition is a job, not a
  session.
- **Long measurement** is a scheduled job plus one issue per measurement.
- **A lane that holds several issues today** finishes what it holds, takes no new issue, and exits; each next piece
  of work starts as a new session on its own issue.

## When your issue is done

1. **Check it is done.** Each done condition has its evidence link, and no issue you created under it is open. An
   issue closed with your own child open, or with a condition handed on and not yet done, counts as not done.
2. **Record it.** Post `LEDGER done <ISSUE> <each done condition and its evidence> <evidence link>` and set the issue
   to done with `--reason` naming the same links. Then end as below.

## Ending, done or blocked

1. **Hand on each Envoy role you hold.** If its duty ended with your work, retire it: delete its row in the record
   and say so in your ledger line. If its duty goes on, ask the holder the record's roles table names (else the
   record owner) to take it, and confirm with `xd://envoy_role_get` that it holds it before you go on. Until one
   holds it, do not exit: a role left on an ended session alerts nobody.
2. **Release your claim** (`dispatch claim --issue <KEY> --release`) and update your
   row in the record (`dispatch doc-edit` against a fresh token).
3. **Exit, as your last action.** In tmux: `tmux send-keys -t "$TMUX_PANE" /exit Enter`. It ends the session at once
   and aborts the turn, so make every write first. Outside tmux, end your turn with the ledger line.
