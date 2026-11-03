# dotfiles-follow

Keeps a machine's `~/.dotfiles` on origin's main by fast-forward only, then installs mise's pins.
When it cannot, it changes nothing and tells the box-health owner over Envoy, once per condition.

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

The cutover rule: the dotfiles change only by commit and push, and each machine pulls. On
sami-devbox nothing pulled, and when someone did, nothing ran `mise install`, so a new pin
(knives 3.2.2) failed A2 until a person installed it by hand.

## What one run does

1. `git fetch origin main`.
2. **Refuses** while the checkout holds any change git sees: a tracked file modified, staged,
   deleted or in conflict; an untracked file that no `.gitignore` covers; a merge, rebase or
   cherry-pick in progress. The kept files below are the one exception.
3. **Refuses** when origin/main is not a fast-forward of HEAD (HEAD has commits origin/main lacks).
4. **Refuses** when the commits to take change a kept file that is modified here.
5. `git merge --ff-only origin/main`. An alert when git refuses.
6. After a fast-forward, and on each later run until it passes once at the current HEAD:
   `mise install`, then `devbox/sami-devbox-check --pins` (the check's knives and mise-pin
   lines). An alert when either fails; the next run tries again without a second alert.

It never stashes, resets, rebases, abandons, restores or deletes. A refusal leaves HEAD, the index
and every file as they were.

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
| a change git sees, other than a kept file | `UNCOMMITTED CHANGES` | the checkout is clean |
| HEAD has commits origin/main lacks | `NOT A FAST-FORWARD` | origin/main contains HEAD |
| the commits to take change a modified kept file | `MAIN CHANGES A KEPT FILE` | they no longer do |
| `git merge --ff-only` failed | `FAST-FORWARD FAILED` | a fast-forward passes, or HEAD is origin/main |
| `mise install` or the pin lines failed | `TOOLS NOT INSTALLED` | both pass at the current HEAD |
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

`python3 -m unittest scripts/tests/test_dotfiles_follow.py`: each case builds a throwaway origin
and clone with the real provision and check scripts, and stand-in `mise` and `knives`. Every
refusal case prints its alert and asserts that HEAD, the index and each file are unchanged.
