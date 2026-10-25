# `jj rebase`

The graphs are not permission. In a shared store, check ownership and descendants for every form, `-r` included ([shared store](shared-store-details.md)).

## Source flags

**`-r <rev>`: only the revisions.** Descendants are re-parented onto the old parents. Use it only when every descendant is your own or its owner agreed to the re-parenting.

```
jj rebase -r K -o M

BEFORE        AFTER
M             K'
| L           M
| K    =>     | L'   <- L moved from K to J
|/            |/
J             J
```

**`-s <rev>`: the revision and all descendants.** Several `-s` make each a direct child of the destination.

```
jj rebase -s M -o O

BEFORE        AFTER
O             N'
| N           M'
| M    =>     O
| | L         | L
| K           | K
|/            |/
J             J
```

**`-b <rev>`: the whole branch**, `(dest..rev)::`, the same as `-s 'roots(dest..rev)'`. It is the default (`jj rebase -o dest` means `-b @`), so a bare rebase is not scoped to your work.

```
jj rebase -b M -o O    (same for -b L or -b K)

BEFORE        AFTER
O             N'
| N           M'
| M           | L'
| | L  =>     |/
| K           K'
|/            O
J             J
```

## Destination flags

| Flag | Effect |
|------|--------|
| `-o` (`-d`) | Onto the targets. Their descendants stay. |
| `-A` | Like `-o`, and moves the targets' descendants onto the rebased revisions. |
| `-B` | Onto the targets' parents, then moves the targets and descendants onto the rebased revisions. |

Combine `-A` and `-B` to splice into a position.

## Patterns

```bash
jj rebase -s L -o K -o M                      # merge: L gets parents K and M
jj rebase -r <merge> -o <p1> -o <p2> -o <p3>  # reset a merge's parents (octopus)
jj rebase -r <rev> -A <target>                # insert after target
jj rebase -o 'trunk()'                        # owned, unpublished branch, after the descendant check
jj rebase -s 'roots(trunk()..@)' -o 'trunk()' # roots of the current range with descendants
jj rebase -b <branch-tip> -o <new-base>       # whole feature branch
```
