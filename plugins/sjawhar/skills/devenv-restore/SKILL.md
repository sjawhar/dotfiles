---
description: "Restore dev environment from S3 backup"
---

Restore development environment state from an S3 backup.

**1. Gather all inputs in ONE prompt:**

Ask the user for:
- **S3 base path** (required): e.g., `s3://bucket/users/sami@metr.org/`
- **Machine name** (optional): defaults to current hostname
- **Session date filter** (optional): only restore Claude sessions after this date (YYYY-MM-DD)

Example prompt:
```
To restore your dev environment, I need:

1. S3 base path (e.g., s3://bucket/users/you@example.org/)
2. Machine name [press Enter for current hostname]
3. Session date filter (e.g., 2026-01-15) [press Enter to restore all]
```

**2. Bootstrap dotfiles if needed:**

Before running restore, check if `~/.dotfiles` exists but lacks `.git`:
```bash
if [ -d ~/.dotfiles ] && [ ! -d ~/.dotfiles/.git ]; then
    echo "Bootstrapping dotfiles..."
    cd ~/.dotfiles
    git init
    git remote add origin https://github.com/sjawhar/dotfiles
    git fetch origin main
    git reset --hard origin/main
    ./install.sh
fi
```

**3. List backups and select:**

```bash
uv run ~/.dotfiles/scripts/devenv-backup/devenv.py list-backups --base <s3-base-path> --machine <machine>
```

Show available backups and ask which to restore:
```
Available backups for devpod:
  2026-01-20 (latest)
  2026-01-18
  before-refactor

Which backup? [default: latest]
```

Then proceed immediately - no separate confirmation needed.

**4. Run restore:**

```bash
# Basic restore:
uv run ~/.dotfiles/scripts/devenv-backup/devenv.py restore \
    --base <s3-base-path> \
    --name <backup-name> \
    --machine <machine>

# With session date filter:
uv run ~/.dotfiles/scripts/devenv-backup/devenv.py restore \
    --base <s3-base-path> \
    --name <backup-name> \
    --machine <machine> \
    --sessions-after 2026-01-15
```

This single command:
- Downloads and validates the manifest
- Displays agent instructions (if present - follow them!)
- Clones all repositories to their original paths
- Creates non-default jj workspaces (in parallel)
- Checks out the correct commits in each workspace (in parallel)
- Downloads files to their original locations
- Restores symlinks
- Restores Claude Code session data
- Restores OpenCode session data (from `{base}/opencode/{machine}/` to `~/.local/share/opencode/storage/`)

**5. Follow agent instructions:**

If the manifest contains `agent_instructions`, they'll be displayed prominently. Follow them (typically `~/.dotfiles/install.sh`).

**6. Summary:**

Report:
- Repositories restored
- Workspaces created
- Files/symlinks restored
- Claude Code sessions restored
- OpenCode sessions restored
- Any errors or warnings

Note any uncommitted changes recorded in the manifest.

**Done when:** All repositories cloned, workspaces created, files restored, Claude Code data restored, and OpenCode session data restored.
