# Silent lanes and waits

## A lane that looks stalled

1. Message it before you kill it (`write agent://<id>`, or `envoy_send` for another session, with the full session id; a prefix is refused). Killing it loses everything it learned.
2. Give it about 10 minutes. When its task runs a build or a test suite, give it that build's or suite's last run time (from CI or your own last run) plus 10 minutes.
3. Re-dispatch only on silence, or on a reply saying it is stuck.

- A status of `Running` is not progress. When it would change your next decision, look for fresh tool output (`read proc://<id>`, `history://<id>:-3`) or an observed state change. With neither, use the deadline in step 2 instead of reporting liveness as progress. A known long-running job that keeps showing progress needs none of this.
- To see whether a lane is working, read `proc://`, not `pgrep`: a `task` subagent runs inside its parent's process, so a process-name grep cannot find it.
- A long-running lane's brief tells it to copy each attempt's terminal error line into its report as it happens; a supervised job's output does not last.

## Waits

- The `wait` tool returns when the first job you started settles or a message reaches you. With no job of yours running, it waits for the reply of a running agent you messaged directly; with neither, it returns at once. It does not tell you that the event you want happened.
- Wait on an event with a watcher that has an explicit stop condition: a named bash service whose `ready` log pattern is the terminal event, or a loop whose stop condition you wrote down. Re-issue it after every interruption.
- The stop condition is a terminal event, never silence and never a first line: a stream can go quiet, or print its first line, while the job still runs.
- Make the watcher print its evidence beside the label (the exact job name and its conclusion) and match on the evidence.
- Pin a watcher to one named run. One that follows "the newest run carrying my merge" exits on the first success it sees, which can be another run.
- Never `sleep` in the foreground: it holds the turn, so no Envoy message reaches you until it returns. Yield when idle, and run a timed re-check as a named bash service.

## Stopping work until an event

1. Before you stop, write down the Envoy topic or timed re-check that will resume you, and the artefact the hold waits on.
2. Subscribe when the pull request or issue is opened, not when you stop. The `envoy` skill has the topic for each event and what `envoy_subscribe`'s warnings mean. On a repository without our GitHub App (every upstream one) no topic fires: use `pr-snapshot watch` or another timed re-check.
3. If someone has committed to ping you on this exact event, yield and run no loop. Otherwise pace the re-check to the thing's own settle time, the time it took to change state last time (a CI run's `updatedAt` minus `startedAt` in `gh run list --json startedAt,updatedAt`, a reviewer's last reply delay); a loop that keeps returning the same value is paced wrong.
4. Run the re-check with credentials you already hold. Call `secrets_request` only when an action needs a new credential, and request every key your pending actions need in that one request.

A topic is a wake, not a verdict: it says a run happened, not which run carries your sha, so the watcher still decides.

## Recovering a report

- Recover a truncated or malformed report from its author: a yielded subagent can usually be messaged, and one message gets the findings back. Read its transcript only once it is gone.
- Have the worker write its report to a named absolute filesystem path and return that path.
- Read structured output field by field (`agent://<id>/findings/0/title`). A whole `agent://<id>` read cuts long lines at 768 characters, so parsing it as JSON fails. A failed `agent://` read is not evidence that the lane died: wait for its result, or read `proc://<id>`.
- A re-woken agent's result can be its previous one: read the head or revision it names before you treat it as current.
- A reply that answers an instruction you have since changed may come from a sender behind on its inbox. Compare the newest `at:` stamp in its session file (`~/.omp/agent/sessions/*/*<session-id>*.jsonl`) with the file's last write (`stat -c %y`): hours between them mean messages wait unread.
- A gate's verdict can sit in a tracker issue's artefacts while `agent://` holds nothing. Check `agent://`, the issue's artefacts and `history://` before you re-dispatch.
- When what you need is a time or a state another lane owns, ask that lane before you set a deadline or start polling.
