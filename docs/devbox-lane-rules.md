# Devbox lane rules

Standing rules for a subagent lane dispatched on the shared devbox. A coordinator prepends this
file to every brief, so a lane reads it at boot rather than learning it from a broadcast. A
broadcast reaches only the lanes that happen to be alive; this file reaches every lane that starts.

These are host and coordination rules. They are not a review rubric, and they do not replace the
role prompts in `sjawhar/legion`'s `packages/pi-envoy/roles/`.

## The shared host

Every lane shares one machine with every other lane and with the human's own sessions. Measure
before you consume.

- **One browser suite at a time.** Take the lock for every Playwright or browser run:
  `flock /tmp/dispatch-playwright.lock -c '<your command>'`. Two concurrent suites on this box
  produce `exit 139` segfaults in WebKit and Chromium, and the crash looks like a product defect.
  `pgrep` finding no other run is not a substitute: it cannot stop one from starting during yours.
- **Read the load and memory before a build, a suite or a container, and say what you read.**
  `cut -d' ' -f1-3 /proc/loadavg` and `free -g`. The load measures the whole box, mostly other
  sessions, so it is not a reason to defer the one browser suite your gate needs: the lock above is
  what keeps suites from colliding. Defer optional runs (a whole-package suite where one file would
  do, a second confirmation) while the load is above about 120; run the gate's suite under the lock
  when you hold it. Under that load a run can fail on a timeout: re-run it once before calling it
  red, and report both. A report that claims a number without naming where it came from is an
  unverified claim.
- **Reuse a running Postgres or harness rather than starting another.** A per-lane container costs
  far more than the database it holds, and several lanes already have one running.
- **Nothing outside your own lane.** Do not kill another lane's processes, drop another lane's
  database, or remove another workspace. If something of someone else's is in your way, report it.

## Workspaces

- **Reuse the workspace you were given.** One workspace per lane, not one per subagent. A workspace
  of this repo's canonical checkout costs about 1 GB.
- **Say when you are finished with it**, so the coordinator can forget it. Do not forget or delete a
  workspace you did not create.
- **Never write into a canonical checkout** (`~/src/<repo>`). Use absolute paths in every file
  operation: a relative path from the wrong working directory lands in the canonical checkout
  silently, and it has happened more than once.

## Version control

jj, never git. The repository's own `AGENTS.md` carries the full rules; these are the two that cost
the most when missed.

- **On a published branch, `jj new` is the first command of any change, before any edit.** Otherwise
  an editing tool lands on `@` while `@` is still the pushed commit, and the next push replaces a
  head other people hold.
- **`jj git push --dry-run` before every push, and it must say `move forward`.** Reading the
  direction word from the real push detects damage instead of preventing it. A rebase legitimately
  says `move sideways`; there the proof is payload equality, not the word.

## Reporting

- **When the brief and the source disagree, the source wins - and say so.** A coordinator's brief is
  a dated copy: verdict ids, line numbers, locator strings and issue keys in it may be wrong. Read
  the code, the comment or the forge, follow what you find, and name the difference in your report.
  Lanes catching this have prevented a helper that would have broken every document spec, a review
  round built on a verdict that did not exist, and several wrong line numbers.
- **Every claim names what produced it.** A command and its output, a file and line, a comment URL.
  "Tests pass" without the command is not evidence.
- **Disclose what you could not run**, and what you carried from an earlier head instead of
  re-running. A carried result is a different artefact from a fresh one, and a reader cannot tell
  unless you say which it is.
- **A read-only lane gives the coordinator verbatim comment text**, verdict line first, rather than
  summarising it. The coordinator posts it unchanged and attributes it.
- **A GraphQL mutation (`gh api graphql` with `resolveReviewThread`, `addPullRequestReviewThreadReply`
  and the like) names no repository, so the `gh` shim cannot tell which GitHub App installation to
  use and may pick one that cannot see the pull request: the call fails `NOT_FOUND` while a query on
  the same thread works.** Run it with `GH_REPO=<owner>/<repo>` set. Two lanes and the coordinator
  read that `NOT_FOUND` as a permission they lacked on agent-c#20732 (2026-10-01).

## Shipping

- **Push a ready fix immediately. Do not hold work back to batch it with a later round.** A gate
  re-cites the delta, which is one cheap read; holding a correct fix to save that read trades a
  human-visible merge for machine time.
- **If you are waiting on something, do the next piece of work you already know you should be
  doing.** Waiting is not a state this box pays for.
