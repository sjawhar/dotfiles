---
name: sync-compound-engineering
description: Use when updating compound-engineering after upstream changes. Converts disabled skills to command files and refreshes skill symlinks.
---

# Sync Compound Engineering

Compound Engineering (CE) is automatically synced via `sync-oss` — it fetches the CE repo and runs `vendor-update-ce` as a post-sync hook. This skill documents both the automated flow and manual steps if you need to update CE outside of `sync-oss`.

## Automated (Preferred)

`sync-oss` handles everything:
1. Fetches `vendor/compound-engineering`
2. Runs `vendor-update-ce` as a post_sync hook
3. Converter output → `~/.local/share/ce-opencode/`
4. Enabled commands → `~/.config/opencode/commands/` (with `ce:` prefix)
5. Disabled commands + disabled skills → `~/.config/opencode/commands/ce/`
6. Enabled skills → `~/.config/opencode/skills/ce/`
7. Agents → `~/.config/opencode/agents/`
8. Cleans up stale `compound-engineering/` prefixed directories

## Manual Update

If you want to update CE outside of `sync-oss`:

### Step 1: Update vendor

```bash
cd ~/.dotfiles/vendor/compound-engineering
jj git fetch && jj rebase -d main@origin
```

### Step 2: Run the converter

```bash
bash ~/.dotfiles/scripts/vendor-update-ce
```

This script:
- Runs the CE converter → `~/.local/share/ce-opencode/`
- Symlinks enabled commands (with `ce:` prefix) → `~/.config/opencode/commands/`
- Symlinks disabled commands + disabled skills → `~/.config/opencode/commands/ce/`
- Symlinks enabled skills → `~/.config/opencode/skills/ce/`
- Symlinks agents → `~/.config/opencode/agents/`
- Cleans up stale `compound-engineering/` prefixed directories
