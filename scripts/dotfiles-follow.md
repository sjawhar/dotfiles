# dotfiles-follow

Keeps a machine's `~/.dotfiles` on origin's main by fast-forward only, then installs mise and omp pins.
When it cannot follow or install tools, it tells the box-health owner over Envoy, once per condition.

Armed by `devbox/install.sh` as the `dotfiles-follow` user timer
(`devbox/dotfiles-follow.{service,timer}`), on a Google VM only (`scripts/on-google-vm`:
sami-devbox, dispatch://INFRA-993). The timer has `OnUnitActiveSec=10min`: it starts a run
10 minutes after the previous one started, or at once when that run took longer (on 2026-11-06
from 10:35Z to 12:02Z each run took 14 min 32 s and the next began the second it ended). Other
machines are moved by `scripts/dotfiles-sync`.

```sh
dotfiles-follow                  # one run, as the timer does it; alerts go over Envoy
dotfiles-follow --print-alert    # the same, and print each message
journalctl --user -u dotfiles-follow.service -n 50
jq . /mnt/nvme/$USER/tmp/dotfiles-follow/state.json   # open conditions, last run, last outcome
```

## Why

The cutover rule: the dotfiles change only by commit and push, and each machine pulls. The
follower installs the committed mise and omp pins. The marketplace lock is follow-managed:
local changes are discarded, never published. Restoring it before the next attempt repairs
the dirty-lock anomaly that stopped the follower after a marketplace install on 2026-11-03.

## What one run does

1. `git fetch origin main`.
2. **Refuses** while the checkout holds any change git sees: a tracked file modified, staged,
   deleted or in conflict; an untracked file that no `.gitignore` covers; a merge, rebase or
   cherry-pick in progress. The kept files below are one exception. A tracked file modified in
   the work tree to exactly the bytes and mode origin/main holds is another: its edit is
   discarded right before the fast-forward, which writes the same bytes.

   `omp/plugins/omp-plugins.lock.json` is **follow-managed**. Its pins change only by pulling a
   commit, never by hand-editing a follower's live checkout. Any local lock anomaly, including
   partial JSON, garbage, a wrong version, staged edits or deletion, is restored to HEAD with
   `git checkout HEAD -- omp/plugins/omp-plugins.lock.json` before judging other changes or
   fast-forwarding. Before restoring it, the follower durably clears its tools-success marker
   while holding the state lock, so an interruption cannot lose the retry. Recovery also runs
   when HEAD already equals origin/main. A dirty lock is not a blocker; other dirty files still
   refuse and are not discarded.
3. **Refuses** when origin/main is not a fast-forward of HEAD (HEAD has commits origin/main lacks).
4. **Refuses** when the commits to take change a kept file that is modified here.
5. `git merge --ff-only origin/main`. An alert when git refuses.
6. After a fast-forward or managed-lock recovery, and on later runs until it passes at HEAD:
   `mise install`, `bun install --frozen-lockfile` in `omp/plugins`, then, for each registered
   non-Bun marketplace plugin whose installed version differs from `omp-plugins.lock.json`,
   `shims/omp plugin marketplace update <source>` and `shims/omp plugin upgrade <name>@<source>`.
   A unique `installed_plugins.json` record identifies its source. Lock entries with no record
   are silently skipped: these include vendored plugins such as compound-engineering and new
   marketplace plugins not yet bootstrapped. Bootstrapping stays an `install.sh` job; follow
   only upgrades existing installs. Multiple matching records are a reported ambiguity.
   Each changed registered plugin is attempted even if another fails, with a 60 s command cap
   and 300 s marketplace-phase budget. Normal cleanup tries to restore the tracked lock in a
   `finally`, with saved bytes as fallback if `git checkout --` fails. An interrupted run may
   leave it dirty: the next run restores HEAD before fast-forwarding or retrying installation.
   An upgrade to a catalog version other than the committed pin is a failure too. Finally,
   `devbox/sami-devbox-check --pins` checks knives and mise pins. An alert when any step fails;
   the next run retries, skipping marketplace plugins already at want.
7. Every run, whatever the checkout's state: one pass of the leak check's hook sweep,
   `sweep-hooks.sh` of the leak-check tag in `~/.local/share/public-leak-check-hooks`
   (`devbox/sami-devbox-provision --only prepush` installs it; `--hooks-dir`). It installs the
   pre-push hook in each checkout of a public repository under the sweep's roots that lacks one
   (dispatch://INFRA-993, dispatch://BET-589). A checkout it could not hook, or one it deferred to
   a later pass because the pass had spent its GitHub visibility lookups (dispatch://BET-706), is a
   condition; a deferral clears when a pass defers none. The script loops for ever, so its code up to the
   loop is loaded and `sweep` runs once, and its whole process group is killed past the pass's
   bound. Two kinds of pass:
   - **fast-root pass**, every run: the script's `fast_roots` only, bounded by `--sweep-timeout`
     (420 s).
   - **slow-root pass**, at most once per `--slow-sweep-interval` (3600 s): `fast_roots` and
     `slow_roots` together, bounded by `--slow-sweep-timeout` (1000 s). The state file's
     `slow_sweep_at` records when the last one ended with its summary line, and only such an end
     writes it, so a killed or broken slow-root pass is tried again on the next run. A value that
     is not a number no later than now (missing, text, a bool, `NaN`, `Infinity`, negative, an
     integer past a float's range such as `10**400` either way, or ahead of a clock set back since)
     counts as no slow-root pass: one is due. `slow_sweep_priority_at` (below) is read the same way.

   **The run's deadline.** systemd kills the unit at `TimeoutStartSec=1170` (`--run-limit`). The
   run keeps a monotonic deadline from its start: 1170 s less a reserve for what comes after the
   sweep, three messages (new, escalated, cleared) each sent to the box-health role and, when that
   fails, to the SRE session, at `--http-timeout` (8 s) a request, so 3 x 2 x 8 = 48 s, plus 12 s
   for the state write and the interpreter's start: 60 s. No pass runs past the deadline:
   - a slow-root pass starts only when the time left holds its whole 1000 s, so it is never a
     shortened walk; otherwise the run makes a fast-root pass and the slow-root pass stays due;
   - a fast-root pass is bounded by the smaller of 420 s and the time left;
   - with under 30 s left no pass starts, and `PRE-PUSH HOOK NOT INSTALLED` says no pass ran.

   So a run that spent 119 s on the fetch and 119 s on a refused merge (872 s left) makes a
   fast-root pass, and a later run with its time makes the slow-root pass whole.

   **The tools step and the slow-root pass.** A run whose tools step (6) ran makes no slow-root
   pass: that step's own limits (merge 120 s, mise 600 s, Bun 600 s, marketplace 300 s, pin check
   180 s) leave no room for 1000 s. A slow-root pass that is overdue (none ended in twice the
   interval, or none ever) is decided before the tools step: when the time left holds it, and no
   run has done this in the last `--slow-sweep-interval`, that run leaves the tools step to the
   next run (which retries it at the same HEAD) and makes the slow-root pass. It writes
   `slow_sweep_priority_at` to the state file before the pass starts, so even a run killed during
   the pass counts. So a tools step that keeps failing, or runs after every fast-forward, delays
   the slow roots by two hours at most, never for good; and a slow-root pass that never completes
   (killed, broken, without its summary line, its script missing) takes one run's tools step an
   interval, never every run's: the runs between run the tools step and a fast-root pass.

   Why: on sami-devbox the slow roots, `/mnt/nvme/sami` and `/mnt/durable/sami`, hold about
   20,000 checkouts and millions of directories. On 2026-11-06 the old sweep's cold walk of all
   roots took 434.7 s at load 191; passes over all roots were killed at the 420 s cap and at a
   raised 870 s cap; and at 11:48Z the walk sat in uninterruptible IO wait in a disk stall that
   wedged other sessions' shells. With the timer's back-to-back runs the walk of all roots ran
   almost without a break.

It never stashes, resets, rebases, abandons or deletes. It restores only the follow-managed
marketplace lock before dirty-file checks and after plugin commands. Other edits are discarded
only when origin/main already holds their bytes, right before the fast-forward. Apart from
managed-lock recovery, a refusal leaves HEAD, the index and every file as they were.

## The kept files: SECRET_FILES that git tracks

Step 2 of the cutover (`devbox/sami-devbox-provision`, `SECRET_FILES`) copies files that no
repository holds. Most are gitignored (`omp/*.local.yml`, `.secrets/`, which is a separate
repository, `secrets.human.d/*.local.env`), so git does not see them. One is tracked:
`secrets.env`, the working copy, which differs from its commit on purpose. Without an exception
the follower would refuse on it for ever.

So a path that `SECRET_FILES` names inside the checkout **and** git tracks may be modified (in the
work tree or the index). It may not be deleted, renamed or in conflict. The list is read from
`devbox/sami-devbox-provision` on every run (bash evaluates the array), never copied, so a file
added there is kept here too. Today that is `secrets.env` alone.

`git merge --ff-only` keeps a modified file that the merge does not touch and refuses one that it
touches. The follower checks this first: when the commits to take change a kept file that is
modified here, it refuses with `MAIN CHANGES A KEPT FILE` and names the diff to merge by hand.

## Conditions and messages

| Condition | Headline | Clears when |
| --- | --- | --- |
| a change git sees, other than a kept file or the follow-managed marketplace lock | `UNCOMMITTED CHANGES` | the other checkout paths are clean |
| HEAD has commits origin/main lacks | `NOT A FAST-FORWARD` | origin/main contains HEAD |
| the commits to take change a modified kept file | `MAIN CHANGES A KEPT FILE` | they no longer do |
| managed-lock restoration or `git merge --ff-only` failed | `FAST-FORWARD FAILED` | the managed lock is clean and HEAD is origin/main |
| mise, Bun, a marketplace upgrade/version check or the pin lines failed | `TOOLS NOT INSTALLED` | all pass at the current HEAD |
| the hook sweep is missing, ran past its bound, exited without its summary line, could not hook a public checkout, or deferred one, or the run had no time left for a pass; the message names the kind of pass | `PRE-PUSH HOOK NOT INSTALLED` | a pass ends with 0 failed and 0 deferred; a condition a slow-root pass found clears only on a slow-root pass |
| `git fetch` failed on 3 runs in a row (`--fetch-grace`) | `CANNOT FETCH origin/main` | a fetch passes |

All of one run's new conditions go in one message. Its headline considers **all open conditions**:
if only tools/hooks remain open and HEAD is at origin/main, it says `<host> is on main, but a
post-update step failed`. Any open fetch, dirty-checkout, ancestry, kept-file or merge condition
keeps `DOTFILES NOT FOLLOWING MAIN on <host>`. A condition that still holds after `--escalate`
(2 h) is said once more, with the corresponding post-update or `STILL NOT FOLLOWING MAIN`
headline; a cleared one is said once (`DOTFILES FOLLOWING MAIN AGAIN`). Messages go where every
box watch here sends them: `BOX_HEALTH_OWNER` (a drop-in that `ensure_box_health_owner` writes),
else the `lead-box-health` role, else the SRE session.

The run exits 0 after a refusal: the alert is the report, so `unit-health-watch` does not say the
same thing a second time. A2 (`devbox/sami-devbox-check`, `host_follow`) reads the state file and
passes only when the timer is active, the last run exited 0, and that run, at most 30 minutes ago,
left HEAD on origin/main with no condition open and the tools installed at HEAD.

The run holds an exclusive, non-blocking `flock` on `state.json.lock` beside the state file for
its whole length; a run that cannot take it skips its period. The state is replaced whole at each
commit (a temporary file in the same directory, fsynced, `os.replace`d over `state.json`, then the
directory fsynced), so a reader or a run killed mid-write sees the complete old state or the
complete new one. The lock is a separate file because the state file's inode changes at every
commit.

**Cutover to the separate lock.** A version before this one locks `state.json` itself, and this
one locks `state.json.lock`, so a run of each at the same time would not exclude each other.
Before the first run of this version, let any running old run finish, hand runs included, and
start no old one meanwhile. The timer never starts a run while its last one is still active, so
on sami-devbox, where an old run fast-forwards the checkout to this version itself, only a hand
run can overlap: start no hand run (old or new) from the time the commit lands until
`systemctl --user is-active dotfiles-follow.service` no longer says `activating` and no old hand
run is left (`pgrep -af dotfiles-follow`). After that every run takes the same lock.

## Tests

`python3 -m unittest scripts.tests.test_dotfiles_follow scripts.tests.test_dotfiles_follow_marketplace`:
each case builds a throwaway origin and clone with the real provision and check scripts, and
stand-in `mise`, `knives` and `bun`. Refusal cases assert that HEAD, the index and each file are
unchanged, except for follow-managed lock recovery. Marketplace cases use a stub `shims/omp`
that records commands, changes installed versions and rewrites the tracked lock. They cover
upgrades, Bun/unchanged/unregistered skips, aggregated failures, decoding, ambiguous sources,
alert classification and escalation, timeout cleanup and recovery from arbitrary dirty-lock
contents before a fast-forward or a same-HEAD retry, including an interrupted durable checkpoint.
