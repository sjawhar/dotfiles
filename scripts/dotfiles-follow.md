# dotfiles-follow

Keeps a machine's `~/.dotfiles` on origin's main, writes the machine's knives registry copy again
when `knives/repos.toml`, the workspaces path or the render script changed, then installs mise and
omp pins. A git checkout moves by fast-forward; a colocated jj checkout moves through
`scripts/dotfiles-sync`. When it cannot follow, write the copy or install tools, it writes a report
to its own log, once per condition. It sends no alert and no message
(dispatch://INFRA-2022/comment/0f96263e-1a85-4f29-85b3-ec713e6acf7b).

Armed by `oryx/install.sh` on oryx as the `dotfiles-follow` user timer
(`devbox/dotfiles-follow.{service,timer}`). The timer has `OnUnitActiveSec=10min`: it starts a run
10 minutes after the previous one started, or at once when that run took longer (on 2026-11-06
from 10:35Z to 12:02Z each run took 14 min 32 s and the next began the second it ended).

sami-devbox no longer arms it. Sami's test for a timer on his dev box
(dispatch://INFRA-2022/ask/b5ddab54-8ba0-40e8-b4ba-c2dcb291ca5c, 2026-11-08): "You agents are able
to freely modify my dot files. So these can be just as unauthorized as anything else. ... We don't
need custom timer units for it. Just because something was done in response to a request of mine
doesn't mean I authorized it." On two timers that watched nothing
(dispatch://INFRA-2022/ask/4badbed6-a78d-4405-adcb-cbe2679e12d3, 2026-11-09): "Both go. Stop the
tick too, or move its wake and its readings somewhere that is not a timer on my box." No answer of
his approves this timer. There `~/.dotfiles` moves onto main when `scripts/dotfiles-publish` runs
from it, or by `scripts/dotfiles-sync local`; other machines are moved by `scripts/dotfiles-sync`.

On oryx, the drop-in `oryx/dotfiles-follow.service.d/oryx.conf` runs
`dotfiles-follow --no-hook-sweep`: oryx never had the leak check's hook sweep installed, so the
sweep is off there. Until oryx's user manager reloads its units (`oryx/install.sh` runs
`systemctl --user daemon-reload`), it still runs the drop-in's old command line, which also passes
`--box-health-role legion-home-controller`; the follower accepts that flag and ignores it.

```sh
dotfiles-follow                  # one run, as the timer does it; its reports go to stdout
journalctl --user -u dotfiles-follow.service -n 50
jq . /mnt/nvme/$USER/tmp/dotfiles-follow/state.json   # open conditions, last run, last outcome
ls /mnt/nvme/$USER/tmp/dotfiles-follow/killed-sweeps/  # the logs of the last 5 killed sweep passes
```

That is `~/.dotfiles`' state file, by any spelling of its path. Where `/mnt/nvme/$USER/tmp` is not
writable, it is `dotfiles-follow/state.json` under what the code reads,
`os.environ.get("XDG_STATE_HOME", ~/.local/state)`: `$XDG_STATE_HOME` when that variable is set,
and `~/.local/state` only when it is unset. An `XDG_STATE_HOME` set to an empty value is used as
given, which puts the file at `dotfiles-follow/state.json` relative to the directory the run starts
in. A run of any other checkout with no `--state` keeps a file of its own beside it,
`state-<key>.json`, whose key is the first 16 hex digits of the sha256 of the checkout's resolved
path, its bytes as they are, UTF-8 or not
(`printf %s "$(realpath <checkout>)" | sha256sum | cut -c1-16`), so nothing that run judges or
records reaches `~/.dotfiles`' state. The `killed-sweeps/` directory beside the state files is
one directory for both: every checkout's run keeps its killed passes' logs there, as before.
`--state` names any file, for any checkout; the rule below says what a run of another checkout
records there.

`--no-hook-sweep` skips the hook sweep. It logs that configuration and opens no hook condition.
The default runs the sweep.

## Why

The cutover rule: the dotfiles change only by commit and push, and each machine pulls. The
follower installs the committed mise and omp pins. The marketplace lock is follow-managed:
local changes are discarded, never published. Restoring it before the next attempt repairs
the dirty-lock anomaly that stopped the follower after a marketplace install on 2026-11-03.

## A checkout other than `~/.dotfiles`

Git mode follows any `--repo`, and `DOTFILES_DIR` names the default one. One rule says what a run
of a checkout other than `~/.dotfiles` does:

- **(A) Guarded writes run only for `~/.dotfiles`; additive writes run for any checkout.** A
  guarded write from another checkout would point the machine at that checkout. The guarded
  writes are the knives registry copy (step 6), and the omp rule links and the marketplace plugins
  (step 7). Another checkout logs each one it skips and goes on. The additive writes are
  `mise install` of the checkout's own pins into the shared `MISE_DATA_DIR`, under the install
  lock (step 7); Bun's cache (step 7); and the fast-root pass of the hook sweep (step 8, rule C).
- **(B) Judgments and records go to the state file the run uses.** On a file other than
  `~/.dotfiles`' (its own, by default) another checkout opens and clears its own conditions,
  `TOOLS NOT INSTALLED` included, as `~/.dotfiles` does on its file. It records its own tools pass
  (`tools_ok_head`), and skips its tools step after one at the same HEAD. It never renders, so it
  never judges `KNIVES REGISTRY NOT WRITTEN`. A run of another checkout on `~/.dotfiles`' file,
  named by `--state` (by any spelling of its path), records nothing: it takes that file's lock,
  so it never runs beside the timer's run, and reads the file, but commits nothing to it.
  `~/.dotfiles`' conditions, `tools_ok_head`, `head`, `target`, `last_outcome`,
  `last_run`, `fetch_failures` and slow-sweep times stay byte for byte as they were, commits made
  mid-run included. That run opens and clears no condition, so it writes no report: each problem
  it found is a line of its output that starts `not recorded: <repo> is not ~/.dotfiles, and <file>
  is ~/.dotfiles's state file, which only a ~/.dotfiles run writes. This run found <headline>: `.
  Since it records nothing, it runs its tools step every run until `~/.dotfiles` passes at that
  HEAD; after that it skips the step.
- **(C) Another checkout makes fast-root sweep passes only (step 8).** It makes no slow-root pass,
  and never leaves its tools step to the next run for one.

## Colocated jj checkouts

When `~/.dotfiles` has `.jj` and `jj --version` works, follow runs
`~/.dotfiles/scripts/dotfiles-sync --here ""` while it holds follow's state lock. This is the only
checkout that follow moves this way. The sync keeps the working-copy change in `@`, rebases it
onto `main@origin`, and abandons commits whose changes main already has. Follow never describes,
commits, or pushes.

For this mode, following means git `HEAD` equals `origin/main`. The git dirty-file and kept-file
checks do not apply. A failed `jj git fetch` makes dotfiles-sync exit 75, which follow counts
toward `--fetch-grace`; every other non-zero sync exit is an immediate sync condition. Follow
sets the sync lock wait one second below its sync timeout, so a held lock reports its own refusal.
Follow restores the managed marketplace lock before it calls the sync.

## What one run does

1. A git checkout runs `git fetch origin main`; a colocated jj checkout runs `jj git fetch` through dotfiles-sync.
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
5. `git merge --ff-only origin/main`. A report when git refuses.
6. On every run that leaves HEAD at origin/main (in git mode, with no uncommitted change), after
   any fast-forward or jj move: `scripts/knives-registry-render --if-changed`, bounded by
   `--check-timeout` (180 s). It renders `knives/repos.toml` into `~/.config/knives/repos.toml`
   as `installers/knives.sh` does, and records what the render depends on in
   `~/.config/knives/repos.toml.source-sha256`: the sha256 of the file it rendered, the oh-my-pi
   workspaces path it wrote (`/mnt/nvme/<home name>/omp-worktrees/branches` where the big local
   disk's directory `/mnt/nvme/<home name>` exists, else `~/.worktrees/oh-my-pi`), and the sha256
   of `scripts/knives-registry-render` itself, whose sed is the render's rule. It writes nothing
   while the copy is a plain file and the record holds all three as this run has them. So a
   merged change to the registry reaches the machine with no person's command, and
   `scripts/leak-terms-check`, which reads the same copy, sees it too; a copy rendered while that
   directory was absent is rendered again once it is there; and a merged change to the render
   script renders again although `knives/repos.toml` did not change. A copy that a person edited
   by hand is replaced when the file changes, as an install replaces it, and a symlink at the
   copy's path is replaced by a plain file. A directory there is never replaced: the render
   fails. The first run of this version renders once, since no record of this form exists yet.
   A failed render is a condition and stops nothing: the tools step still runs. A `~/.dotfiles`
   with no `scripts/knives-registry-render` (a main older than the step) or no `knives/repos.toml`
   skips it, and an open condition clears. The render runs only for `~/.dotfiles` (rule A, above):
   another `--repo` (or `DOTFILES_DIR`) logs
   `registry: <repo> is not ~/.dotfiles; knives registry copy not written` and writes no copy.
7. After a fast-forward or managed-lock recovery, and on later runs until it passes at HEAD:
   `scripts/omp-link-rules`, which links `omp/rules/*.md` into `~/.omp/agent/rules/`, only for
   `~/.dotfiles` (rule A, above), as `dotfiles-sync`'s tools step does: a link made from another
   checkout would point every session's rules at it. Another `--repo` logs
   `rules: <repo> is not ~/.dotfiles; omp rules not linked` and goes on. Then
   `mise install`, `bun install --frozen-lockfile` in `omp/plugins`, then, only for `~/.dotfiles`,
   for each registered non-Bun marketplace plugin below the lowest version its lock entry requires,
   `shims/omp plugin marketplace update <source>` and `shims/omp plugin upgrade <name>@<source>`.
   `installers/omp.sh` links `~/.omp/plugins` to `~/.dotfiles/omp/plugins`, so an upgrade from
   another checkout would write this machine's plugins. Another `--repo` logs
   `plugins: <repo> is not ~/.dotfiles; marketplace plugins not upgraded`, runs no omp command
   and goes on to the pin check.
   `mise install` holds `$MISE_DATA_DIR/.dotfiles-install.lock` (waiting for it at most
   `--mise-timeout`), the lock `oryx/install.sh`'s `op` step, `dotfiles-sync`'s tools step,
   `agent-session`, `shims/knives`, `installers/mise.sh` and `installers/librarian-harness-host.sh`
   hold too, so installs on one `MISE_DATA_DIR` never run side by side and a launch waits
   for a running install. That `mise install` alone gets `DOTFILES_INSTALL_LOCK_HELD=1`, so a knives
   call under it that lacks its pin exits 127 at once instead of waiting on the lock. mise, Bun,
   the omp commands and the pin check get the `MISE_CACHE_DIR` that the checkout's `.bashrc` gives a
   shell (read under `--check-timeout`), so this mise and a session's take the same per-version lock
   in mise's cache. A unique `installed_plugins.json` record identifies its source. Lock entries
   with no record are silently skipped: these include vendored plugins such as compound-engineering
   and new marketplace plugins not yet bootstrapped. Bootstrapping stays an `install.sh` job; follow
   only upgrades existing installs. Multiple matching records are a reported ambiguity.
   Each below-entry registered plugin is attempted even if another fails, with a 60 s command cap
   and 300 s marketplace-phase budget. Normal cleanup tries to restore the tracked lock in a
   `finally`, with saved bytes as fallback if `git checkout --` fails. An interrupted run may
   leave it dirty: the next run restores HEAD before fast-forwarding or retrying installation.
   omp installs a catalog plugin only at the version its catalog resolves now, so no machine can go
   back to an older entry. A plugin at or above its entry gets no upgrade or version failure. A
   plugin below its entry refreshes its catalog and upgrades, and is a version failure only if it is
   still below afterward; a failed omp command is a failure whatever the version. Dotted-number
   strings compare numerically; any other pair must be equal JSON values of the same type, and
   object key order does not matter. An empty or missing entry is handled as before. Every omp
   command removes `OMP_PROFILE`, `PI_PROFILE`, and
   `PI_CODING_AGENT_DIR` from its environment. If
   `${XDG_CONFIG_HOME:-~/.config}/omp/profile` contains `legion`, follow does not run omp and
   reports the plugin step as failed. Finally, `devbox/sami-devbox-check --pins` checks knives and
   mise pins. A report when any step fails; the next run retries any marketplace plugin still below
   its entry. The run judges `TOOLS NOT INSTALLED` and records a pass at HEAD (`tools_ok_head`) in
   the state file it uses (rule B, above). In `~/.dotfiles` the pass line is
   `tools: mise install, frozen Bun install, marketplace pins and sami-devbox-check --pins passed`,
   and in another `--repo` it is
   `tools: mise install, frozen Bun install and sami-devbox-check --pins passed; rules and
   marketplace plugins skipped: <repo> is not ~/.dotfiles`. The line names the tools step's parts
   whether or not each had work: it names the frozen Bun install when `omp/plugins/package.json` is
   missing and no Bun command ran, and the marketplace pins when there is no lock or no plugin was
   below its entry, so no omp command ran.
8. Unless `--no-hook-sweep` is set, every run makes one pass of the leak check's hook sweep,
   `sweep-hooks.sh` of the leak-check tag in `~/.local/share/public-leak-check-hooks`
   (`devbox/sami-devbox-provision --only prepush` installs it; `--hooks-dir`). It installs the
   pre-push hook in each checkout of a public repository under the sweep's roots that lacks one
   (dispatch://INFRA-993, dispatch://BET-589). A checkout it could not hook, or one it deferred to
   a later pass because the pass had spent its GitHub visibility lookups (dispatch://BET-706), is a
   condition; a deferral clears when a pass defers none. The script loops for ever, so its code up to the
   loop is loaded and `sweep` runs once, and its whole process group is killed past the pass's
   bound. A killed pass's log, and what it printed, are kept in `killed-sweeps/` beside the state
   file, the newest five only (`KILLED_SWEEP_LOGS`), and the report names the file: the pass's own
   log is in a temporary directory, and journald may drop the unit's lines (on 2026-11-20 it
   dropped them for the hour around a killed slow-root pass). Two kinds of pass:
   - **fast-root pass**, every run: the script's `fast_roots` only, bounded by `--sweep-timeout`
     (420 s).
   - **slow-root pass**, only in a run of `~/.dotfiles` (rule C, above) and at most once per
     `--slow-sweep-interval` (3600 s): `fast_roots` and `slow_roots` together, bounded by
     `--slow-sweep-timeout` (1000 s). The state file's `slow_sweep_at` records when the last one
     ended with its summary line, and only such an end writes it, so a killed or broken slow-root
     pass is tried again on the next run. A value that
     is not a number no later than now (missing, text, a bool, `NaN`, `Infinity`, negative, an
     integer past a float's range such as `10**400` either way, or ahead of a clock set back since)
     counts as no slow-root pass: one is due. `slow_sweep_priority_at` (below) is read the same way.

   **The run's deadline.** systemd kills the unit at `TimeoutStartSec=1170` (`--run-limit`). The
   run keeps a monotonic deadline from its start: 1170 s less 12 s (`STATE_RESERVE`) for the state
   write and the interpreter's start. No pass runs past the deadline:
   - a slow-root pass starts only when the time left holds its whole 1000 s, so it is never a
     shortened walk; otherwise the run makes a fast-root pass and the slow-root pass stays due;
   - a fast-root pass is bounded by the smaller of 420 s and the time left;
   - with under 30 s left no pass starts, and `PRE-PUSH HOOK NOT INSTALLED` says no pass ran.

   So a run that spent 119 s on the fetch and 119 s on a refused merge (920 s left) makes a
   fast-root pass, and a later run with its time makes the slow-root pass whole.

   **The tools step and the slow-root pass.** A run whose tools step (7) ran makes no slow-root
   pass: that step's own limits (merge 120 s, the rule links 180 s, the `.bashrc` read 180 s, mise
   600 s, Bun 600 s, marketplace 300 s, pin check 180 s) leave no room for 1000 s. In a run of
   `~/.dotfiles`, a slow-root pass that is overdue (none ended in twice the interval, or none ever)
   is decided before the tools step: when the time left holds it, and no run has done this in the
   last `--slow-sweep-interval`, that run leaves the tools step to the next run (which retries it at
   the same HEAD) and makes the slow-root pass. Another checkout makes no slow-root pass (rule C),
   so it never leaves its tools step for one. The `~/.dotfiles` run writes `slow_sweep_priority_at`
   to the state file before the pass starts, so even a run killed during the pass counts. So a
   tools step that keeps failing, or runs after every fast-forward, delays the slow roots by two
   hours at most, never for good; and a slow-root pass that never completes (killed, broken,
   without its summary line, its script missing) takes one run's tools step an interval, never
   every run's: the runs between run the tools step and a fast-root pass.

   Why: on sami-devbox the slow roots, `/mnt/nvme/sami` and `/mnt/durable/sami`, hold about
   20,000 checkouts and millions of directories. On 2026-11-06 the old sweep's cold walk of all
   roots took 434.7 s at load 191; passes over all roots were killed at the 420 s cap and at a
   raised 870 s cap; and at 11:48Z the walk sat in uninterruptible IO wait in a disk stall that
   wedged other sessions' shells. With the timer's back-to-back runs the walk of all roots ran
   almost without a break.

Git mode never stashes, resets, rebases, or abandons. jj mode leaves `@` as the working copy;
dotfiles-sync rebases it and abandons commits whose changes main already has. dotfiles-follow
never describes, commits, or pushes. Both modes restore only the follow-managed marketplace lock
before dirty-file checks and after plugin commands. Other edits are discarded only when origin/main
already holds their bytes, right before the fast-forward. Apart from managed-lock recovery, a
refusal leaves HEAD, the index and every file as they were. Outside the checkout, and only from
`~/.dotfiles` (rule A), step 6 writes `~/.config/knives/repos.toml` and its record only when the
copy is missing or not a plain file, or `knives/repos.toml`, the workspaces path or the render
script changed since the last render, and step 7 links rules into `~/.omp/agent/rules/` and
upgrades marketplace plugins in `~/.omp/plugins`. A run that writes the copy logs it, and the
footer of any report that run writes says so.

## The kept files: SECRET_FILES that git tracks

Step 2 of the cutover (`devbox/sami-devbox-provision`, `SECRET_FILES`) copies files that no
repository holds. Most are gitignored (`omp/*.local.yml`, `.secrets/`, which is a separate
repository, `secrets.human.d/*.local.env`), so git does not see them. One is tracked:
`secrets.env`, the working copy, which differs from its commit on purpose. Without an exception
the follower would refuse on it for ever.

So a path that `SECRET_FILES` names inside the checkout **and** git tracks may be modified (in the
work tree or the index). It may not be deleted, renamed or in conflict. The list is read from
`devbox/sami-devbox-provision` on every run (bash evaluates the array), never copied, so a file
added there is kept here too. Today that is `secrets.env` alone. Its paths are relative to
`$HOME`, and one is inside the checkout when its directory, resolved, lies in the checkout's real
path: a `~/.dotfiles` that is a symlink to a directory outside `$HOME` keeps `secrets.env` too,
whether the run names it `~/.dotfiles` or by its real path.

`git merge --ff-only` keeps a modified file that the merge does not touch and refuses one that it
touches. The follower checks this first: when the commits to take change a kept file that is
modified here, it refuses with `MAIN CHANGES A KEPT FILE` and names the diff to merge by hand.

## Conditions and reports

| Condition | Headline | Clears when |
| --- | --- | --- |
| a change git sees, other than a kept file or the follow-managed marketplace lock | `UNCOMMITTED CHANGES` | the other checkout paths are clean |
| `dotfiles-sync`, except a failed `jj git fetch`, refuses a colocated jj working-copy move | `DOTFILES SYNC REFUSED` | the sync moves `~/.dotfiles` and git `HEAD` equals `origin/main` |
| HEAD has commits origin/main lacks | `NOT A FAST-FORWARD` | origin/main contains HEAD |
| the commits to take change a modified kept file | `MAIN CHANGES A KEPT FILE` | they no longer do |
| managed-lock restoration or `git merge --ff-only` failed | `FAST-FORWARD FAILED` | the managed lock is clean and HEAD is origin/main |
| the rule link or a marketplace upgrade/version check (both in `~/.dotfiles` only), mise, Bun or the pin lines failed | `TOOLS NOT INSTALLED` | all pass at the current HEAD, in a run that records in that state file (rule B) |
| `scripts/knives-registry-render --if-changed` failed or ran past `--check-timeout`; the report holds its output and says whether the copy kept what it held | `KNIVES REGISTRY NOT WRITTEN` | a later `~/.dotfiles` run on main renders, or finds nothing to write |
| the hook sweep is missing, ran past its bound, exited without its summary line, could not hook a public checkout, or deferred one, or the run had no time left for a pass; the report names the kind of pass | `PRE-PUSH HOOK NOT INSTALLED` | a pass ends with 0 failed and 0 deferred; a condition a slow-root pass found clears only on a slow-root pass |
| `git fetch`, or `jj git fetch` in a colocated checkout, failed on 3 runs in a row (`--fetch-grace`) | `CANNOT FETCH origin/main` | a fetch passes |

All of one run's new conditions go in one report. Its headline considers **all open conditions**:
if only tools, registry or hooks conditions remain open and HEAD is at origin/main, it says
`<host> is on main, but a post-update step failed`. Any open fetch, dirty-checkout, sync,
ancestry, kept-file, or merge condition keeps `DOTFILES NOT FOLLOWING MAIN on <host>`. A
condition that still holds is not reported again; a cleared one is reported once
(`DOTFILES FOLLOWING MAIN AGAIN`). A report goes to the run's own output, which the unit's journal
keeps, and nowhere else: the follower sends no Envoy message and no alert. The state file holds
the conditions still open.

The run exits 0 after a refusal: the refusal is a condition it reports, not a failed run.

The run holds an exclusive, non-blocking `flock` on `state.json.lock` (another checkout's:
`state-<key>.json.lock`) beside the state file for its whole length; a run that cannot take it
skips its period. A run of another checkout on its own file never holds `~/.dotfiles`' lock; one on
`~/.dotfiles`' file by `--state` holds it, and records nothing there (rule B). The
state is replaced whole at each commit (a temporary file in the same directory, fsynced,
`os.replace`d over the state file, then the directory fsynced), so a reader or a run killed
mid-write sees the complete old state or the complete new one. The lock is a separate file because
the state file's inode changes at every commit.

**Cutover to the separate lock.** A version before this one locks `state.json` itself, and this
one locks `state.json.lock`, so a run of each at the same time would not exclude each other.
Before the first run of this version, let any running old run finish, hand runs included, and
start no old one meanwhile. The timer never starts a run while its last one is still active, so
on sami-devbox, where an old run fast-forwards the checkout to this version itself, only a hand
run can overlap: start no hand run (old or new) from the time the commit lands until
`systemctl --user is-active dotfiles-follow.service` no longer says `activating` and no old hand
run is left (`pgrep -af dotfiles-follow`). After that every run takes the same lock.

## Tests

`python3 -m unittest scripts.tests.test_dotfiles_follow scripts.tests.test_dotfiles_follow_marketplace scripts.tests.test_omp_marketplace_installer scripts.tests.test_dotfiles_follow_jj scripts.tests.test_dotfiles_install_lock`:
the follower cases build a throwaway origin and clone with the real provision and check scripts,
and stand-in `mise`, `knives` and `bun`. Refusal cases assert that HEAD, the index and each file
are unchanged, except for follow-managed lock recovery. Marketplace cases use a stub `shims/omp`
that records commands, changes installed versions and rewrites the tracked lock. They cover
upgrades, a plugin above its entry (no omp command), one upgraded above its entry (a pass), one
still below after its upgrade (a failure), 0.3.10 above 0.3.9, versions that are not dotted
numbers, Bun/unchanged/unregistered skips, aggregated failures, decoding, ambiguous sources, report
classification, timeout cleanup and recovery from arbitrary dirty-lock contents
before a fast-forward or a same-HEAD retry, including an interrupted durable checkpoint. The
installer module runs `install_marketplace_plugin` and its version reads and comparison, taken
from `installers/omp.sh`, under `set -euo pipefail` with a stub `omp` on `PATH`: the same version
cases, and a failed catalog refresh, install or upgrade still reports omp's exit status and leaves
the lock as committed. Both modules check the shared version table,
`scripts/tests/marketplace_version_cases.py`, for dotted-number strings and other equal JSON values
of the same type, including reordered objects. Its per-tool empty rows record follow's comparison
result and the installer's existing empty-argument result after it reads null, missing, or empty
strings. Number spelling, such as 1.10 against 1.1, can differ between jq and Python and is out of
scope, so no shared row requires agreement on it. Its raw rows are written into the two files as
JSON text, so the installer reads them as stored.
The jj cases use a real colocated jj checkout whose `@` holds two local files, and check that the
files keep their bytes and nothing is pushed.
The registry cases (`KnivesRegistry`, and `JjKnivesRegistry` in the jj module) run the real
`scripts/knives-registry-render` and work out the copy it must leave without running it
(`RegistryCopy`, which `test_dotfiles_tools` and `test_knives_installer` use too). A pull that
changes `knives/repos.toml` writes the copy in the run that fast-forwards, and the next run writes
nothing. A hand-edited copy stays until the file changes. A copy rendered while the big disk's
directory was absent is rendered again once it is there, and the next run writes nothing; that
case points `KNIVES_REGISTRY_NVME_ROOT`, which replaces `/mnt/nvme` in the render, at a scratch
tree. A pull that changes only the render script (one comment line) renders again, writing the
record and not the copy, whose bytes are the same, and the next run writes nothing.
The first run of this version at HEAD renders once. A read-only `~/.config/knives` opens
`KNIVES REGISTRY NOT WRITTEN` and stops neither the fast-forward nor the tools step, and the next
render clears it. A refused jj sync leaves that condition open.
The harness makes its shared state file `DEFAULT_STATE`, so a case that runs another clone on it
runs it on `~/.dotfiles`' file, where it records nothing (rule B).
`test_a_checkout_that_is_not_home_dotfiles_writes_no_copy` follows another clone, by `--repo` and
by `DOTFILES_DIR`, and leaves the copy as it was, and its control
`test_home_dotfiles_writes_a_stale_copy_by_any_spelling_of_its_path` writes it from `~/.dotfiles`.
`test_another_checkout_leaves_an_open_registry_condition_open` opens `KNIVES REGISTRY NOT WRITTEN`
from `~/.dotfiles`, and a run of another clone on that state file leaves it open, clearing nothing;
its control, the next `~/.dotfiles` run, clears it.
`RuleLinks` commits a linker stand-in that records each checkout it runs for.
`test_only_home_dotfiles_links_the_omp_rules`: a run of another clone does not call it, logs
`rules: <repo> is not ~/.dotfiles; omp rules not linked` and passes mise and Bun; a run of
`~/.dotfiles` calls it. `test_another_checkout_leaves_an_open_tools_condition_open`: a failed link
in `~/.dotfiles` opens `TOOLS NOT INSTALLED`, a run of another clone on that state file whose mise
and Bun pass leaves it open, and the next `~/.dotfiles` run, whose link passes, runs the linker once
and clears it; one row's clone is at `~/.dotfiles`' HEAD and the other's at a HEAD of its own. In
another clone the pass line names the skipped rules and marketplace plugins.
`test_only_home_dotfiles_upgrades_marketplace_plugins` (`MarketplacePlugins`): another clone and
`~/.dotfiles` each hold lock entries above the installed records. The run of the other clone makes
no `shims/omp` call and logs
`plugins: <repo> is not ~/.dotfiles; marketplace plugins not upgraded`; its control, the run of
`~/.dotfiles`, refreshes each catalog and upgrades each plugin as before.
`test_a_run_of_another_checkout_whose_tools_step_ran_sweeps_fast_roots_only` (`SlowRoots`): a
run of another clone whose tools step ran makes no slow-root pass.
`test_a_home_dotfiles_symlinked_out_of_home_keeps_secrets_env_moves_and_renders` keeps
`secrets.env`, fast-forwards and renders with `~/.dotfiles` a symlink out of `$HOME`, named either
way, and the plain `~/.dotfiles` as before.
`DefaultStateFile` runs `main()` with no `--state` and `DEFAULT_STATE` at a scratch path.
`test_another_checkout_leaves_the_home_dotfiles_state_file_byte_identical`: `~/.dotfiles` opens
`UNCOMMITTED CHANGES` in the default file, and a run of another clone, by `--repo` and by
`DOTFILES_DIR`, leaves that file's bytes as they were and records its own head in a file of its own
beside it. Its control, `test_home_dotfiles_writes_the_default_state_file_and_no_other`:
`~/.dotfiles`, by default, by `--repo ~/.dotfiles` and as `$HOME/../<home's name>/.dotfiles` (a
spelling pathlib keeps), writes the default file and no other.
`test_each_other_checkout_keeps_one_state_file_of_its_own`: one clone, run twice and then by
another spelling of its path, uses one file, and a second clone at the same HEAD uses another.
`test_a_checkout_path_that_is_not_utf8_keeps_a_state_file_of_its_own`: for a clone at a path that
is not UTF-8, and a UTF-8 control, the file's key is the one the shell command above prints, and
`main()`, in a child whose stdout refuses what is not UTF-8, ends 0 and records the clone's head in
that file.
`OneRule.test_the_one_rule` is the rule as one table. Its rows are `~/.dotfiles` and another clone
at `~/.dotfiles`' HEAD, each on its own state file and on `DEFAULT_STATE` by `--state` (spelled
through `..`); each row runs `main()` with the hook sweep on: a failing tools step on a fresh
state file, a dirty checkout, a failing tools step, a passing one, two more runs, then
`~/.dotfiles`' pass at that HEAD on its file and two more runs. Its cells: (A) whether a run
wrote the registry copy, linked the rules or called `shims/omp`; (B) which of
`UNCOMMITTED CHANGES` and `TOOLS NOT INSTALLED` its reports opened and cleared and which it
logged as not recorded, whether `tools_ok_head` and `last_run` were recorded, whether the state
file kept its bytes through every run of the row's checkout, which of `~/.dotfiles`' conditions,
opened on its file between those runs, its own next pass cleared, and how many of two runs ran
the tools step after the checkout's own pass and then after `~/.dotfiles`' pass; (C) the kind of
sweep pass of the first run, whether its tools step ran, and how many slow-root passes the
checkout made. Another clone on its own file opens and clears both conditions, records its pass
and skips its tools step after it (review 20's first case), and its first run on a fresh file
makes a fast-root pass and runs its tools step (review 20's second case), where `~/.dotfiles`
makes the slow-root pass and leaves its tools step. Another clone on `DEFAULT_STATE` leaves that
file byte-identical, opens and clears nothing, logs both problems as not recorded, runs its tools
step on both runs before `~/.dotfiles`' pass and on neither after it.
`OneRule.test_another_checkout_on_a_shared_state_file_leaves_the_registry_condition_open`:
`~/.dotfiles` opens `KNIVES REGISTRY NOT WRITTEN` on a `--state` file that is not `DEFAULT_STATE`,
a run of another clone on that file, which records its run there (rule B), leaves it open
(review 21), and its control, a `~/.dotfiles` run that renders, clears it.
`SendsNothing` runs the follower as each unit runs it (`main()` in a child): the dev box's unit with
a `BOX_HEALTH_OWNER`, and oryx's drop-in as loaded before this change, with `--box-health-role`.
With `ENVOY_URL` at a local listener and an `ENVOY_TOKEN` set, each opens a condition, and the
listener must receive no request and the report must be in the run's output.
The install-lock cases run follow's `install_tools`, `dotfiles-sync --tools`, `agent-session`, the
two mise installers and `shims/knives` with one stand-in `mise` that fails when two installs run at
once. They check that the installs never overlap, that a knives call under an install does not
wait on its lock, and that each of follow's tools commands (mise, Bun, the marketplace step's omp
calls, the pin check) gets the `.bashrc`'s `MISE_CACHE_DIR`.
