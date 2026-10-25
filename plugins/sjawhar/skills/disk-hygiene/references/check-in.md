# Phase 2: Check-in, releases and the protected set

`S` is as set in SKILL.md. Send this to each titled live session on the machine:

```
Disk cleanup check-in. I am inventorying every workspace/worktree/checkout on this box and
deleting what no live session needs. I will NOT touch any directory a live session reports as
in use. Please do NOT delete anything yourself; just answer.
Envoy shows your cwd as: <dir>
1. Your cwd (confirm/correct) plus any other checkout/dir you or your subagents use right now.
2. Dirs you created that are DONE and safe to delete (merged, PR closed, scratch). Paths.
3. Other dead weight you know of (eval logs, docker images/containers you own, caches).
"cwd only: <path>" is a complete answer. Silence after 20 min = cwd only.
```

Drain replies with `envoy_inbox` (one reply arrives per turn) into three lists: **live** (protected), **released** (the owner gave the workspace or checkout up; `apply` still checks it before it deletes it), **holds** (a human said to keep it). A live claim beats a release from someone else. An explicit hold beats everything.

- Capture each release: `python3 $S release-capture --path P --repo R --kind git|jj [--name N] [--releases releases.json]`. Without `--releases`, paste the output into the file. `plan` only reports releases; `apply` acts on each once.
- Apply refuses a release if HEAD or the admin id changed, if HEAD reaches unpushed commits, or if content exists only on disk (a gitignored `.env` or `.venv/` blocks it). If only the directory mtime changed, apply skips the entry and leaves it in the releases file for the next `apply` run.
- Build the protected set: `python3 $S protected --fixed fixed.json > protected.json`, with the check-in's live paths and holds under `"protected"` in the fixed file. The script adds bind-mount sources, knives checkouts and process cwds.
- Protect exact paths: a broad prefix like `~/inspect` blocks its owner's releases. For a released child of a protected path, set `"subpath_release": true` on its entry in the releases file (or its plan item); the live-process check still applies.
- Only the idle threshold (`--fresh-hours`) protects a workspace or checkout that runs through `jj -R` from elsewhere, because no process has its cwd there.
- The script reads the protected file again before each item, so add a late reply mid-run.
