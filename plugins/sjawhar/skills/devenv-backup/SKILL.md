---
description: "Backup dev environment to S3"
---

Capture development environment state and upload to S3.

**1. Generate manifest:**
```bash
uv run ~/.dotfiles/scripts/devenv-backup/devenv.py manifest > /tmp/devenv-manifest.json
```

Parse the JSON and read the `uncommitted` section.

**1b. Display captured structure:**

Show the user a tree view of all captured workspaces with their change IDs and bookmarks:

```
Captured structure:
├── pivot/
│   ├── default        @ knynrwpv
│   ├── compare-plots  @ xlksqkzq  [compare-plots]
│   └── tui-fixes      @ xnussuvl  [tui-fixes]
├── iam/
│   └── default        @ oklnutnl
└── dotfiles/
    └── default        @ ltxnrnzv
```

Format: `workspace_name @ change_id [bookmark_name]` (bookmark only if one exists)

If files were captured (manifest version 2), also show:
```
Files to backup:
├── ~/
│   └── notes.txt (1.2 KB)
└── ~/pivot/
    ├── architecture.md (5.4 KB)
    └── pro_critique/
        └── critique.txt (2.1 KB)
```

If symlinks were captured, show them:
```
Symlinks to backup:
├── ~/.jjconfig.toml -> .dotfiles/.jjconfig.toml
├── ~/.gitconfig -> .dotfiles/.gitconfig
└── ~/jj/CLAUDE.md -> .dotfiles/.claude/project-instructions/jj.md
```

**Note on stale workspaces:** If jj commands fail with "workspace is stale" errors, run `jj workspace update-stale` in that workspace directory first. The Python script handles this automatically, but you may encounter it when running manual jj commands.

**2. Analyze uncommitted changes (if any):**

If there are uncommitted changes, analyze them **in parallel** using subagents:
- For each uncommitted change, `cd` to the workspace directory, then run `jj diff -r <change_id>`
- Check if the change is empty (no file changes) AND has no description
- For non-empty changes: generate a suggested commit description based on the diff
- Default branch name = workspace name

**Auto-skip empty changes:** If a change has no file modifications AND no description (typically the current working copy `@` commit), automatically skip it without prompting the user. These are just jj's empty working copy changes.

**CRITICAL: Skip commits that ARE main:** Before processing any change:
1. Run `jj log -r <change_id>` in the workspace directory
2. If the change's commit is directly on `main` (i.e., it IS main, not just branched from it), **skip it entirely**
3. These commits are immutable and cannot be modified, described, or pushed to a new branch
4. The manifest's `uncommitted` list may include the current working copy which happens to be sitting on main - these must be skipped

**3. Present results to user:**

Only show changes that need user action (non-empty AND (need a description OR have not been pushed to remote)):
```
| Workspace | Change ID | Current Description | Suggested Description | Branch | Action |
|-----------|-----------|--------------------|-----------------------|--------|--------|
| pivot     | xlksqkzq  | (empty)            | Add compare plots...  | compare-plots | push |
| iam       | efgh5678  | WIP                | Fix auth validation   | iam | skip |
```

If all uncommitted changes were auto-skipped (all empty with no description, or on main), skip directly to step 6.

Ask the user to confirm or edit:
- Description to use for commit
- Branch name for push
- Action: **push** (describe + push), **discard** (abandon), or **skip** (leave as-is)

**4. Execute actions:**

For each change based on user's decision, first `cd` to the correct workspace directory:

- **push**:
  ```bash
  cd <workspace_directory>

  # First, check existing bookmarks
  jj bookmark list

  # Describe the change (if needed)
  jj describe -r <change_id> -m "<description>"

  # Handle bookmark:
  # - If bookmark already exists and is "ahead by N commits": just push it
  # - If bookmark exists and is synced: skip (already pushed)
  # - If no bookmark exists: create it
  # - NEVER set bookmarks on commits that are ancestors of main

  # Only create if bookmark doesn't exist:
  jj bookmark set <branch> -r <change_id>

  jj git push --bookmark <branch>
  ```
- **discard**: `jj abandon <change_id>`
- **skip**: do nothing

**5. Re-verify manifest:**

Run manifest again:
```bash
uv run ~/.dotfiles/scripts/devenv-backup/devenv.py manifest > /tmp/devenv-manifest.json
```

If `uncommitted` is still non-empty, show the remaining items and ask user if they want to proceed anyway or resolve them.

**6. Get S3 base path from user:**

Ask for:
- S3 base path (e.g., `s3://bucket/users/sami@metr.org/`)
- The backup will be stored at `{base}/{machine}/{name}/`
- Claude Code data will be stored at `{base}/claude-code/{machine}/`
- OpenCode session data will be stored at `{base}/opencode/{machine}/`

Optional settings (mention defaults):
- `--name`: Backup name (default: today's date YYYY-MM-DD)
- `--machine`: Machine identifier (default: hostname)
- `--agent-instructions`: Optional freeform text to display during restore
- `--timeout`: Overall timeout in seconds (default: 120s)

**7. Recommend dry-run first:**

```bash
uv run ~/.dotfiles/scripts/devenv-backup/devenv.py backup --base <s3-base-path> --dry-run
```

This shows what would be uploaded without actually uploading.

**8. Run backup:**

```bash
uv run ~/.dotfiles/scripts/devenv-backup/devenv.py backup --base <s3-base-path>

# With optional flags:
uv run ~/.dotfiles/scripts/devenv-backup/devenv.py backup \
  --base <s3-base-path> \
  --name 2026-01-20 \
  --machine devpod \
  --agent-instructions "Run install.sh after restore"

# With custom timeout (default: 120 seconds):
uv run ~/.dotfiles/scripts/devenv-backup/devenv.py --timeout 300 backup --base <s3-base-path>
```

This single command:
- Generates the manifest (with files by default, root_dir from $HOME)
- Uploads manifest.json to `{base}/{machine}/{name}/`
- Uploads files to `{base}/{machine}/{name}/files/`
- Syncs Claude Code data to `{base}/claude-code/{machine}/` (only session data, not git-tracked config or credentials)
- Syncs OpenCode session data to `{base}/opencode/{machine}/` (session, message, part, project, and todo directories from `~/.local/share/opencode/storage/`)

**9. Summary:**

Print:
- Backup location
- Number of workspaces captured
- Number of files uploaded (if any)
- Number of symlinks captured (if any)
- Claude Code data synced
- OpenCode session data synced
- Any uncommitted changes that were skipped

**Done when:** Backup is uploaded to S3.
