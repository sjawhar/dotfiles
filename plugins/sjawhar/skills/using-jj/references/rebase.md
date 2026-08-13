# Covers `jj rebase` source and destination flags, graph diagrams, merge commits, and common patterns.

## Rebase

`jj rebase` moves revisions to different parents while preserving their diffs. The behavior varies significantly depending on which source flag you use.

### Source flags: -r vs -s vs -b

**`-r` (revisions only) -- extracts and re-parents children**

Rebases ONLY the specified revisions. Descendants are re-parented onto the revision's OLD parents, filling the "hole". The revision is "extracted" from the graph.

```
jj rebase -r K -o M

BEFORE        AFTER
M             K'
|             |
| L           M
| |    =>     |
| K           | L'    <-- L was re-parented from K to J (K's old parent)
|/            |/
J             J
```

Use `-r` when you want to move a commit without bringing its descendants. Common for rewriting octopus merge parents.

**`-s` (source + descendants) -- moves subtree intact**

Rebases the specified revision AND all its descendants. The whole subtree moves together.

```
jj rebase -s M -o O

BEFORE        AFTER
O             N'
|             |
| N           M'
| |           |
| M    =>     O
| |           |
| | L         | L
| |/          | |
| K           | K
|/            |/
J             J
```

Use `-s` when you want to transplant a whole feature branch. Multiple `-s` arguments make each a direct child of dest (flattening).

**`-b` (branch) -- moves everything not already on dest**

Rebases the whole "branch" relative to the destination: the set `(dest..rev)::` -- meaning revisions that aren't ancestors of dest, plus ALL their descendants.

Equivalent to: `jj rebase -s 'roots(dest..rev)' -o dest`

```
jj rebase -b M -o O    (same result if you said -b L or -b K)

BEFORE        AFTER
O             N'
|             |
| N           M'
| |           |
| M           | L'
| |    =>     |/
| | L         K'
| |/          |
| K           O
|/            |
J             J
```

Use `-b` when rebasing after a fetch -- it moves your whole branch onto the updated trunk. **This is the default** when no flag is specified (`jj rebase -o dest` implies `-b @`).

### Destination flags: -o vs -A vs -B

| Flag | Behavior |
|------|----------|
| `-o/--onto` (alias `-d`) | Place onto targets. Existing descendants of targets unaffected. |
| `-A/--insert-after` | Like `-o`, but also rebases targets' existing descendants onto the rebased revisions. |
| `-B/--insert-before` | Rebases onto targets' parents, then rebases targets and their descendants onto the rebased revisions. |

`-A` and `-B` can be combined to splice revisions into a specific location in the graph.

### Creating merge commits

Repeat `-o` to create a merge:

```bash
jj rebase -s L -o K -o M     # L now has parents K and M
jj rebase -r @ -o A -o B -o C   # Reset @'s parents to A, B, C (octopus merge)
```

### Common patterns

```bash
# Rebase current branch onto updated trunk (most common)
jj rebase -o 'trunk()'

# Rebase all local branches onto trunk
jj rebase -s 'roots(trunk()..@)' -o 'trunk()'

# Reset a merge commit's parents (e.g., drop branches from octopus)
jj rebase -r <merge> -o <parent1> -o <parent2> -o <parent3>

# Extract a commit from middle of chain (descendants stay)
jj rebase -r <middle> -o <new-parent>

# Move whole feature branch onto new base
jj rebase -b <branch-tip> -o <new-base>
```
