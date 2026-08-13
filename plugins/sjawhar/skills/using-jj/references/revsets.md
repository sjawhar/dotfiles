# Covers jj revset selection syntax and the all-branches rebase example.

## Revsets

Revsets are a functional query language for selecting commits. Most commands accept `-r <revset>`.

| Revset | Meaning |
|--------|---------|
| `@` | Working copy change |
| `@-` | Parent of `@` |
| `@+` | Child of `@` |
| `trunk()` | Default remote bookmark, or `root()` if none resolves |
| `root()` | Root commit (`zzzzzzzz`) |
| `mine()` | Changes authored by current user |
| `heads(all())` | All branch heads |
| `::x` | Ancestors of x |
| `x::` | Descendants of x |
| `x..y` | Ancestors of y that are not ancestors of x |
| `ancestors(x, depth)` | Ancestors with depth limit |
| `description(substring:x)` | Changes with x in description |
| `bookmarks()` | Changes with bookmarks |
| `remote_bookmarks()` | Changes with remote bookmarks |

**Rebase all branches onto updated trunk:**
```bash
jj rebase -s 'roots(trunk()..@)' -o 'trunk()'
```
