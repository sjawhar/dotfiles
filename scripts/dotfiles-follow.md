# dotfiles-follow

Keeps a machine's `~/.dotfiles` on origin's main by fast-forward only, then installs mise and omp pins.
When it cannot follow or install tools, it tells the box-health owner over Envoy, once per condition.

Armed by `devbox/install.sh` as the `dotfiles-follow` user timer
(`devbox/dotfiles-follow.{service,timer}`), every 10 minutes, on a Google VM only
(`scripts/on-google-vm`: sami-devbox, dispatch://INFRA-993). Other machines are moved by
`scripts/dotfiles-sync`.

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
   `mise install`, `bun install --frozen-lockfile` in `omp/plugins`, then, for each non-Bun plugin
   whose installed version differs from `omp-plugins.lock.json`, `shims/omp plugin marketplace
   update <source>` and `shims/omp plugin upgrade <name>@<source>`. Sources come from the local
   `installed_plugins.json`; an unknown source is reported with instructions to run `install.sh`
   once. Each changed plugin is attempted even if another fails, with a 60 s command cap and
   300 s marketplace-phase budget. Normal cleanup tries to restore the tracked lock in a
   `finally`, with saved bytes as fallback if `git checkout --` fails. An interrupted run may
   leave it dirty: the next run restores HEAD before fast-forwarding or retrying installation.
   An upgrade to a catalog version other than the committed pin is a failure too. Finally,
   `devbox/sami-devbox-check --pins` checks knives and mise pins. An alert when any step fails;
   the next run retries, skipping marketplace plugins already at want.
7. Every run, whatever the checkout's state: one pass of the leak check's hook sweep,
   `sweep-hooks.sh` of the leak-check tag in `~/.local/share/public-leak-check-hooks`
   (`devbox/sami-devbox-provision --only prepush` installs it; `--hooks-dir`). It installs the
   pre-push hook in each checkout of a public repository under the sweep's roots that lacks one
   (dispatch://INFRA-993, dispatch://BET-589). The script loops for ever, so its code up to the
   loop is loaded and `sweep` runs once. The pass is bounded by `--sweep-timeout` (120 s; a full
   pass on sami-devbox, /mnt/nvme/sami included, took 21 s on 2026-11-03), and its whole process
   group is killed past it.

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
| the hook sweep is missing, ran past its bound, or could not hook a public checkout | `PRE-PUSH HOOK NOT INSTALLED` | a pass ends with 0 failed |
| `git fetch` failed on 3 runs in a row (`--fetch-grace`) | `CANNOT FETCH origin/main` | a fetch passes |

All of one run's new conditions go in one message, `DOTFILES NOT FOLLOWING MAIN on <host>`. A
condition that still holds after `--escalate` (2 h) is said once more (`STILL NOT FOLLOWING MAIN`);
a cleared one is said once (`DOTFILES FOLLOWING MAIN AGAIN`). Messages go where every box watch
here sends them: `BOX_HEALTH_OWNER` (a drop-in that `ensure_box_health_owner` writes), else the
`lead-box-health` role, else the SRE session.

The run exits 0 after a refusal: the alert is the report, so `unit-health-watch` does not say the
same thing a second time. A2 (`devbox/sami-devbox-check`, `host_follow`) reads the state file and
passes only when the timer is active, the last run exited 0, and that run, at most 30 minutes ago,
left HEAD on origin/main with no condition open and the tools installed at HEAD.

## Tests

`python3 -m unittest scripts.tests.test_dotfiles_follow scripts.tests.test_dotfiles_follow_marketplace`:
each case builds a throwaway origin and clone with the real provision and check scripts, and
stand-in `mise`, `knives` and `bun`. Refusal cases assert that HEAD, the index and each file are
unchanged, except for follow-managed lock recovery. Marketplace cases use a stub `shims/omp`
that records commands, changes installed versions and rewrites the tracked lock. They cover
upgrades, skips, aggregated failures, decoding, unknown sources, timeout cleanup and recovery
from arbitrary dirty-lock contents before a fast-forward or a same-HEAD retry.
