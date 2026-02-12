---
name: sync-compound-engineering
description: Use when updating compound-engineering after upstream changes. Converts disabled skills to command files and refreshes skill symlinks.
---

# Sync Compound Engineering

The installer (`installers/opencode.sh`) symlinks CE commands and enabled skills but skips disabled skills (`disable-model-invocation: true`). This skill fills the gap: disabled skills get converted to command files.

## Step 1: Update vendor

```bash
cd ~/.dotfiles/vendor/compound-engineering
jj git fetch && jj rebase -d main@origin
```

## Step 2: Create command files for disabled skills

Find disabled skills missing a command file, show the list, confirm with user, then create:

```bash
CE_PLUGIN="$HOME/.dotfiles/vendor/compound-engineering/plugins/compound-engineering"

for skill_dir in "$CE_PLUGIN/skills"/*/; do
    [ -d "$skill_dir" ] || continue
    skill_md="${skill_dir}SKILL.md"
    [ -f "$skill_md" ] || continue
    grep -q "disable-model-invocation: true" "$skill_md" 2>/dev/null || continue

    name=$(awk '/^---$/{n++; if(n==2) exit} n>=1 && /^name:/{sub(/^name: */, ""); print}' "$skill_md")
    cmd_file="${CE_PLUGIN}/commands/${name}.md"
    [ -f "$cmd_file" ] && continue

    echo "NEW: $name"

    desc=$(awk '/^---$/{n++; if(n==2) exit} n>=1 && /^description:/{sub(/^description: */, ""); print}' "$skill_md")
    hint=$(awk '/^---$/{n++; if(n==2) exit} n>=1 && /^argument-hint:/{sub(/^argument-hint: */, ""); print}' "$skill_md")

    {
        echo "---"
        echo "name: ${name}"
        echo "description: ${desc}"
        [ -n "$hint" ] && echo "argument-hint: ${hint}"
        echo "---"
        awk '/^---$/{n++; if(n==2){found=1; next}} found{print}' "$skill_md"
    } > "$cmd_file"
done
```

These files are untracked in the vendor git repo — they survive `jj git fetch` as local additions.

## Step 3: Refresh skill symlinks

Re-run the installer to pick up any new/removed skills and commands:

```bash
bash ~/.dotfiles/installers/opencode.sh
```
