# backup-shared-state

Copies the devbox's single-copy shared state to the transfer bucket: the grading
farm's git repository (bundle of all refs), the shared compute ledger, and the
chief of staff's records under `~/src/scrum`.

## Why it exists

On 2026-10-25 two things several streams write were found to exist on one disk:

- `/mnt/nvme/sami/grade-farm`, a git repository with 28 commits whose remote
  `theorem-labs/bet-grade-farm` **does not exist** (`gh repo view` answers
  "Could not resolve to a Repository"). It holds the farm's scheduler, box
  setup, retention pass, and that day's fix for a defect that failed every grade
  sent to a box without its image.
- `/mnt/nvme/sami/compute-inventory.csv`, 356 KB and 1,874 lines, which records
  which VM belongs to which stream. Several streams append to it; nobody owns it.

That same day `/mnt/nvme` fell from 201 GB free to 131 GB in twenty minutes, and
a throwaway script deleted 120 grading-run directories by mistake.

This is **not** version control. It bounds the loss to the interval between runs
instead of to the life of the box. The real fix for each item is a repository
with a reachable remote.

## Running it

```sh
scripts/backup-shared-state            # writes to gs://autoformalization-transfer/devbox-state
BACKUP_BUCKET=gs://other/path scripts/backup-shared-state
```

Every run writes new timestamped objects, so a damaged local state cannot
overwrite a good remote copy. Prune old objects in the bucket, never from here.
It exits non-zero if any item failed, and names the item on stderr.

## On a timer

`systemctl --user` on that box was degraded on 2026-10-25 and refused to install
units ("Transport endpoint is not connected"), so run it from a session's own
loop or from cron until that is fixed. Hourly is enough for the ledger, which is
written a few times a day; the farm repository changes less often.

## Adding an item

Add one `copy_repo`, `copy_file` or `copy_tree` call with a comment saying what
would be lost and how it was found. An item nobody can name a loss for does not
belong here: this is for state with no second copy, not for scratch.
