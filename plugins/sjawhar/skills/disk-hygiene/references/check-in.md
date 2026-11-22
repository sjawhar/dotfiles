# Phase 2: check-in, releases and the protected set

Read this in Phase 2, before you send the check-in. `S` is set in SKILL.md.

1. Send this to each titled live session `envoy_sessions` lists on this machine (not subagents: their parent answers for them):

   ```
   Disk cleanup check-in. I am inventorying every workspace/worktree/checkout on this box and
   deleting what no live session needs. I will not touch any directory a live session reports as
   in use. Please do not delete anything yourself; just answer.
   Envoy shows your cwd as: <dir>
   1. Your cwd (confirm/correct) plus any other checkout/dir you or your subagents use right now.
   2. Dirs you created that are done and safe to delete (merged, PR closed, scratch). Paths.
   3. Other dead weight you know of (eval logs, docker images/containers you own, caches).
   "cwd only: <path>" is a complete answer. Silence after 20 min = cwd only.
   ```

2. Drain replies with `envoy_inbox` (one reply arrives per turn) into three lists: live (protected), released (the owner gave the path up), holds (a human said to keep it).
   - A live claim beats someone else's release.
   - An explicit hold beats everything.
   - Add a late reply's paths to the protected file, even while `apply` runs.
3. Capture each release: `python3 $S release-capture --path P --repo R --kind git|jj [--name N] --releases releases.json`.
4. Find the Legion daemon paths from the registry file itself: in a Legion pane `XDG_STATE_HOME` points inside the daemon's state, so `legion legions` prints `[]` while a daemon runs.

   ```bash
   (set -eo pipefail; r=~/.local/state/legion/legions.json; [ -e "$r" ] || exit 0
    jq -nr 'input | if type == "array" then .[] else error("registry is not a list") end | .stateDir // error("entry without stateDir")' "$r"
    jq -r '.[] | .configPath // error("entry without configPath")' "$r" | while IFS= read -r c; do git -C "$(dirname "$c")" rev-parse --show-toplevel; done)
   ```

   - Exit 0 with no output means no daemon registered.
   - A nonzero exit means you do not know a daemon's paths: report the error.
   - After a nonzero exit, remove nothing until you know the Legion daemon paths.
   - Protect the registry directory `~/.local/state/legion/` and every path printed: each `stateDir` holds live issues' workspaces, and the daemon reads more of its config checkout than the one file.
5. Build the protected set: `python3 $S protected --fixed fixed.json > protected.json`.
   - Put under `"protected"` in the fixed file the live paths, the holds and the Legion paths, each absolute (`protected` does not expand `~`).
   - The script adds bind-mount sources, process cwds and the knives fork checkouts: the checkouts of the forks Sami's `knives` tool manages, as `knives_checkouts` in `$S` collects them.
   - A workspace or checkout that a process uses through `jj -R` from elsewhere has no process cwd in it, so of the script's own checks only the idle threshold (`plan --fresh-hours`) protects it.
     - Put each one a reply names under `"protected"`.
   - Protect exact paths: a broad prefix such as `~/inspect` blocks its owner's releases.
   - For a released child of a protected path, set `"subpath_release": true` on its releases entry or plan item; the live-process check still applies.
   - Never set `"subpath_release"` for a path that is, is inside, or holds a Legion path.
