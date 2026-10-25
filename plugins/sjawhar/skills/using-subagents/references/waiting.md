# Silent lanes, waits and parks

Answer on the channel a message arrived on: `hub send` reaches only agents listed in your session's roster, addressed by roster name, so addressing a peer by its session id returns `Unknown agent`, and a parent cannot delegate the relay — it sends over Envoy itself, with the full ids. A full id is required: a prefix is refused as "no live session".

## A lane that looks stalled

- Message it before you kill it. A subagent deep in legitimate work loses everything it learned when you kill it: `envoy_send` a status request, give it about 10 minutes (longer when its task involves builds or long test suites), and re-dispatch only on silence or a reply confirming it is wedged.
- A status of `Running` is not progress evidence. When it would change your next decision, look for fresh tool output or an observed state transition; with neither, use the deadline above instead of reporting liveness as progress. A known long-running job that keeps showing progress needs none of this.
- To see whether a lane is really working, read `hub ps`, not `pgrep`: a lane running its long operations as hub-supervised processes is invisible to a process-name grep.
- A supervised process's failure evidence is volatile, so a long-running lane's brief says to copy each attempt's terminal error line into its report as it happens.

## Waits

- The `wait` tool with no exit condition returns on any event, including the echo of your own writes, so it does not tell you the event you want happened: on a PR you are editing your own comments come back as events within seconds, and a wait watching only subagent jobs returns a snapshot immediately.
- Use a supervised watcher with an explicit exit condition — a named process whose output you wait on for a pattern, or a loop with a stop condition you wrote down. Re-issue it after every interruption, and never read a returned wait as "the thing I was waiting for happened".
- The stop condition is a terminal event, never silence and never a first line, because a stream can go quiet, or print its first line, while the job is still running.
- A pattern wait matches the watcher's output, which is a claim about the process rather than the process. Make the watcher print the evidence beside the label — the exact job name and its conclusion — and pattern on the evidence string.
- A watcher that follows a moving target ("the newest non-failed run carrying my merge") exits on the first success it sees, which can be a different run, so pin it to one named run.
- Never wait in the foreground: a bash `sleep` holds the turn, so Envoy messages cannot reach the session until it returns. Yield when idle and put a timed re-check in a supervised background process.
- An agent revived with `hub send` is no longer a job, so `hub wait` returns on its own window rather than the agent's. Read the tail of its transcript instead (`history://<id>:-3`) and keep `wait` for jobs.

## Parks

Before you stop work to wait on an event (park), write down which Envoy topic or timed re-check will resume you. A hold names the artifact it waits on, because a hold condition you cannot name the artifact for is not one. Subscribe when the PR or issue is opened, not when you park:

- a PR: `notifications.github.<owner>.<repo>.pr.<N>.>` — with the `.>`, because the base alone gets only opened/synchronize/closed;
- a workflow run with no PR: `notifications.github.<owner>.<repo>.workflow.<file>.>`;
- a Dispatch issue: `notifications.dispatch.issue.<KEY>.>`.

A subscription being accepted does not make it a wake source: read `envoy_subscribe`'s Warnings line, and if it says there is no GitHub event for that repo in retention ("is the App installed there?"), the topic will never fire. That is the normal case for upstream repos our GitHub App is not installed on, so wait on an upstream PR with a timed re-check such as `pr-snapshot watch`.

A topic is a wake, not a verdict: it says a run happened, not which run carries your sha, so a watcher still decides. Before any loop, ask whether someone has committed to ping you on this exact event — if so, yield and run no loop. Otherwise pace the re-check to the thing's own settle time; a loop whose every iteration returns the same value is telling you the pace is wrong. Run the re-check with credentials you already hold. Request a new credential (`secrets_request`) only when an action needs it, and request every key your pending actions need in that one request.

## Recovering a report

- A truncated or malformed report is recovered from its author, not excavated from the record: a yielded subagent is usually idle and revivable, and one message gets the findings back in prose. Excavate only once it is gone.
- Prevent it: have the worker write its report to a named absolute path and yield that path, so you read an artifact instead of trusting a reference to a message. Without that, a revived subagent says "everything else from my prior report stands" about a report that never reached you. Name a real filesystem path, not a `/tmp/omp-local/session` URI: a dispatched agent's write tool refuses that scheme, so never promise a file its own tools cannot create.
- Read structured output field by field (`agent://<id>/findings/0/title`): reading `agent://<id>` whole returns a preview whose long lines are cut at 768 characters, so parsing it as JSON fails with "Unterminated string". A failed `agent://` lookup is not evidence the lane died — wait for its result, or check the process.
- A result can be real and stale: a re-woken agent's delivered result, or its `agent://<id>`, can be its previous yield, so read the head or revision the result names before treating it as current.
- Do not treat a gate (a review lane) as lost because one store is empty: a verdict can have settled as a tracker artifact while `agent://` holds nothing. Check `agent://`, the tracker issue's artifacts and `history://` before re-dispatching.
- When what you need is a time or a state another lane owns, ask that lane before you set a deadline or start polling for it.
