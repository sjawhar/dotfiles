---
name: sync-oss
description: Sync all tracked OSS repos — fetch, rebase/merge, resolve conflicts, summarize changes
---

# Sync OSS

Sync all tracked repositories defined in `~/.dotfiles/sync-repos.json`. Handles vendor repos, forks, and personal repos across machines.

## Step 1: Run the sync script

```bash
bash ~/.dotfiles/scripts/sync-oss 2>sync-oss-stderr.log | tee /tmp/sync-oss-results.json
```

Read the JSON output from `/tmp/sync-oss-results.json`. Also check `sync-oss-stderr.log` for any warnings.

## Step 2: Report clean results

Present a summary table:

| Repo | Status | Notes |
|------|--------|-------|
| ... | synced / up_to_date / missing / ... | ... |

## Step 3: Handle conflicts

For each repo with `"status": "conflict"`:

1. Navigate to the repo path
2. The script already reverted the failed rebase/merge — the repo is in a clean state
3. Re-attempt the sync manually:
   - For repos without `upstream`: `jj git fetch && jj rebase -d main@origin`
   - For repos with `upstream` + `bookmark`:
     - Check `method` and `target` from `sync-repos.json`
     - If `method` is `rebase`: `jj rebase -b <bookmark> -d <base>`
     - If `method` is `merge`: `jj new <bookmark> <base>`
4. Use the `resolve-conflicts` skill to handle any conflicts:
   - Map both sides before touching anything
   - Classify conflict type (path, content, path+content, delete vs modify)
   - Resolve each file, documenting your choice
   - Verify with `jj status` — no conflicts remaining
5. If resolution is too complex or risky, flag it for manual review — don't guess

## Step 4: Summarize upstream changes

For each repo that was synced (has `before` and `after` in the results):

```bash
cd <repo_path>
jj log -r '<before>::<after>' --no-graph --limit 20
```

Summarize:
- **New features and bug fixes** — brief list
- **Breaking changes** — call these out prominently
- **Config changes** — flag any new options, changed defaults, or deprecations that might need attention in dotfiles or other configs

## Step 5: Final report

Summarize everything:
- Which repos synced cleanly
- Which had conflicts and how they were resolved
- Which need manual attention
- Notable upstream changes (especially breaking changes and config updates)

## Done when

All repos are either synced or have their conflict status clearly reported. No repo is left in a broken state.
